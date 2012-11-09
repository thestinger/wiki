#!/usr/bin/env python3

import hmac
import os.path as path
from datetime import datetime
from hashlib import sha256
from os import urandom

import pygit2 as git
import scrypt
import sqlalchemy as sql
from bottle import get, post, redirect, response, request, run, static_file, view
from docutils.core import publish_string

engine = sql.create_engine("sqlite:///wiki.sqlite3", echo=True)
metadata = sql.MetaData()

users = sql.Table("users", metadata,
                  sql.Column("username", sql.String, primary_key = True),
                  sql.Column("email", sql.String, nullable = False),
                  sql.Column("email_verified", sql.Boolean, nullable = False,
                             default = False),
                  sql.Column("password_hash", sql.Binary, nullable = False),
                  sql.Column("password_salt", sql.Binary, nullable = False))

generated = sql.Table("generated", metadata,
                      sql.Column("name", sql.String, primary_key = True),
                      sql.Column("revision", sql.String, primary_key = True),
                      sql.Column("content", sql.String, nullable = False))

metadata.create_all(engine)

connection = engine.connect()

repo = git.init_repository("repo", True)

try:
    with open("key.rnd", "rb") as f:
        KEY = f.read()
except FileNotFoundError:
    with open("key.rnd", "wb") as f:
        KEY = urandom(256)
        f.write(KEY)

def generate_mac(s):
    return hmac.new(KEY, s.encode(), sha256).hexdigest()

def make_login_token(username):
    return "-".join((generate_mac(username), username))

def check_login_token(token):
    "Return the username if the token is valid, otherwise None."
    mac, username = token.split('-', 1)
    if hmac.compare_digest(mac, generate_mac(username)):
        return username

def get_page_revision(name, revision):
    return repo[repo[revision].tree[name + ".rst"].oid].data

def get_html_revision(name, revision):
    s = sql.select([generated.c.content],
                   (generated.c.name == name) & (generated.c.revision == revision))
    result = connection.execute(s).first()

    if result is None:
        settings = {"stylesheet_path": "/static/html4css1.css,/static/main.css",
                    "embed_stylesheet": False}
        content = publish_string(get_page_revision(name, revision), writer_name="html",
                                 settings_overrides=settings)
        connection.execute(generated.insert().values(name=name,
                                                     revision=revision,
                                                     content=content))
        return content

    return result[0]

@get('/')
def index():
    return static_file("index.html", root="static")

@get('/page/<filename>.rst')
def rst_page(filename):
    response.content_type = "text/x-rst; charset=UTF-8"
    revision = request.query.get("revision", repo.head.hex)
    return get_page_revision(filename, revision)

@get('/page/<filename>.html')
def html_page(filename):
    revision = request.query.get("revision", repo.head.hex)
    return get_html_revision(filename, revision)

@get('/static/<filename>.css')
def css(filename):
    return static_file(filename + ".css", root="static")

def page_log(page, commits):
    # TODO: currently ignores the possibility of moved files
    # TODO: does not consider removed/recreated files
    for commit in commits:
        tree = commit.tree
        if page not in tree:
            continue
        parent_tree = commit.parents[0].tree

        diff = parent_tree.diff(tree)
        files = diff.changes["files"]
        if any(x[0] == page for x in files):
            yield commit

def log():
    commits = repo.walk(repo.head.oid, git.GIT_SORT_TIME)

    try:
        page = request.query["page"] + ".rst"
        commits = page_log(page, commits)
    except KeyError:
        pass

    return {"log": [{"message": c.message,
                     "author": c.author.name,
                     "time": datetime.fromtimestamp(c.author.time).isoformat() + "Z",
                     "revision": c.hex}
                    for c in commits]}

@get('/log.html')
@view('log.html')
def html_log():
    return log()

@get('/log.json')
def json_log():
    return log()

@get('/edit/html/<filename>')
@view("edit.html")
def html_edit(filename):
    token = request.get_cookie("token")
    username = check_login_token(token)
    form_token = make_login_token(username + "-edit")

    try:
        blob = get_page_revision(filename, repo.head.oid)
    except KeyError: # filename.rst not in tree
        blob = ""

    return dict(content=blob, token=form_token)

def edit(name, message, page, username):
    email, = connection.execute(sql.select([users.c.email],
                                           users.c.username == username)).first()
    signature = git.Signature(username, email)

    oid = repo.write(git.GIT_OBJ_BLOB, page)
    bld = repo.TreeBuilder(repo.head.tree)
    bld.insert(name + '.rst', oid, 100644)
    tree = bld.write()
    repo.create_commit('refs/heads/master', signature, signature, message,
                       tree, [repo.head.oid])

@post('/edit/html/<filename>')
def form_edit(filename):
    message = request.forms["message"]
    page = request.forms["page"]
    form_token = request.forms["token"]
    token = request.get_cookie("token")

    username = check_login_token(token)

    if check_login_token(form_token) != username + "-edit":
        return

    edit(filename, message, page, username)

    redirect('/page/{}.html'.format(filename))

@post('/edit/json/<filename>')
def json_edit(filename):
    message = request.json["message"]
    page = request.json["page"]
    token = request.json["token"]

    username = check_login_token(token)

    if username is None:
        return {"error": "invalid login token"}

    edit(filename, message, page, username)

@get('/register.html')
def html_register():
    return static_file("register.html", root="static")

def register(username, password, email):
    salt = urandom(64)
    hashed = scrypt.hash(password, salt)
    connection.execute(users.insert().values(username=username,
                                             email=email,
                                             password_hash=hashed,
                                             password_salt=salt))

@post('/register.html')
def form_register():
    email = request.forms["email"]
    password = request.forms["password"]
    username = request.forms["username"]

    register(username, password, email)

    response.set_cookie("token", make_login_token(username))
    redirect("/")

@post('/register.json')
def json_register():
    try:
        username = request.json["username"]
        password = request.json["password"]
        email = request.json["email"]
    except KeyError as e:
        return {"error": "missing {} key".format(e.args[0])}

    try:
        register(username, password, email)
    except sql.exc.IntegrityError:
        return {"error": "username already registered"}

    return {"token": make_login_token(username)}

@get('/login.html')
def html_login():
    return static_file("login.html", root="static")

def login(username, password):
    select = sql.select([users.c.password_hash, users.c.password_salt],
                        users.c.username == username)
    hashed, salt = connection.execute(select).first()

    if not hmac.compare_digest(hashed, scrypt.hash(password, salt)):
        raise ValueError("invalid password")

    return make_login_token(username)

@post('/login.html')
def form_login():
    username = request.forms["username"]
    password = request.forms["password"]

    response.set_cookie("token", login(username, password))
    redirect("/")

@post('/login.json')
def json_login():
    try:
        username = request.json["username"]
        password = request.json["password"]
    except KeyError as e:
        return {"error": "missing {} key".format(e.args[0])}

    try:
        return {"token": login(username, password)}
    except TypeError:
        return {"error": "invalid username"}
    except ValueError:
        return {"error": "invalid password"}

def main():
    if 'refs/heads/master' not in repo.listall_references():
        author = git.Signature('wiki', 'danielmicay@gmail.com')
        tree = repo.TreeBuilder().write()
        repo.create_commit('refs/heads/master', author, author,
                           'initialize repository', tree, [])

    run(host='localhost', port=8080, reloader=True)

if __name__ == '__main__':
    main()
