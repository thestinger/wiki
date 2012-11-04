#!/usr/bin/env python3

import hmac
import os.path as path
from base64 import b64encode
from hashlib import sha256
from os import urandom

import pygit2 as git
import scrypt
import sqlalchemy as sql
from bottle import get, post, response, request, run, static_file, template
from docutils.core import publish_file, publish_string

engine = sql.create_engine("sqlite:///wiki.sqlite3", echo=True)
metadata = sql.MetaData()
users = sql.Table("users", metadata,
                  sql.Column("username", sql.String, primary_key = True),
                  sql.Column("email", sql.String, nullable = False),
                  sql.Column("email_verified", sql.Boolean, nullable = False,
                             default = False),
                  sql.Column("password_hash", sql.Binary, nullable = False))
metadata.create_all(engine)

connection = engine.connect()

repo = git.init_repository("repo", False)

if 'refs/heads/master' not in repo.listall_references():
    author = git.Signature('wiki', 'danielmicay@gmail.com')
    tree = repo.TreeBuilder().write()
    repo.create_commit('refs/heads/master', author, author,
                       'initialize repository', tree, [])

def generate_html_page(name):
    publish_file(source_path=path.join("repo", name + ".rst"),
                 destination_path=path.join("generated", name + ".html"),
                 writer_name="html")

# TODO: this should be a persistent key, generated with something like openssl
KEY = b64encode(urandom(256))

def generate_mac(s):
    return hmac.new(KEY, s.encode(), sha256).hexdigest()

def make_login_token(username):
    return "|".join((generate_mac(username), username))

def check_login_token(token):
    "Return the username if the token is valid, otherwise None."
    mac, username = token.split('|', 1)
    if hmac.compare_digest(mac, generate_mac(username)):
        return username

def get_page_revision(filename, revision):
    return repo[repo[revision].tree[filename + ".rst"].oid].data

@get('/page/<filename>.rst')
def rst_page(filename):
    response.content_type = "text/x-rst; charset=UTF-8"
    revision = request.query.get("revision", repo.head.oid)
    return get_page_revision(filename, revision)

@get('/page/<filename>.html')
def html_page(filename):
    revision = request.query.get("revision")

    if revision is None:
        return static_file(filename + '.html', root="generated")
    else:
        return publish_string(get_page_revision(filename, revision), writer_name="html")

@get('/log.json')
def log():
    commits = repo.walk(repo.head.oid, git.GIT_SORT_TIME)

    try:
        page = request.query["page"] + ".rst"
        commits = filter(lambda c: page in c.tree, commits)
    except KeyError:
        pass

    return {"log": [{"message": c.message,
                     "author": c.author.name,
                     "revision": c.hex}
                    for c in commits]}

@get('/edit/html/<filename>')
def html_edit(filename):
    # TODO: fill out the content and anti-CSRF token
    return template("edit.html", content="", token="")

def edit(filename, message, page, username):
    email, = connection.execute(sql.select([users.c.email],
                                           users.c.username == username)).first()
    signature = git.Signature(username, email)

    with open(path.join("repo", filename + '.rst'), "w") as f:
        f.write(page)
    generate_html_page(filename)

    oid = repo.write(git.GIT_OBJ_BLOB, page)
    bld = repo.TreeBuilder()
    bld.insert(filename + '.rst', oid, 100644)
    tree = bld.write()
    repo.create_commit('refs/heads/master', signature, signature, message,
                       tree, [repo.head.oid])

@post('/edit/html/<filename>')
def form_edit(filename):
    message = request.forms["message"]
    page = request.forms["page"]
    token = request.get_cookie("token")

    # TODO: check the form's anti-CSRF token

    username = check_login_token(token)

    edit(filename, message, page, username)

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
    hashed = scrypt.encrypt(b64encode(urandom(64)), password, maxtime=0.5)
    connection.execute(users.insert().values(username=username,
                                             email=email,
                                             password_hash=hashed))

@post('/register.html')
def form_register():
    email = request.forms["email"]
    password = request.forms["password"]
    username = request.forms["username"]

    register(username, password, email)

    response.set_cookie("token", make_login_token(username))

@post('/register.json')
def json_register():
    try:
        username = request.json["username"]
        password = request.json["password"]
        email = request.json["email"]
    except KeyError as e:
        return {"error": "missing {} key".format(e.args[0])}

    register(username, password, email)

    return {"token": make_login_token(username)}

@get('/login.html')
def html_login():
    return static_file("login.html", root="static")

def login(username, password):
    hashed, = connection.execute(sql.select([users.c.password_hash],
                                            users.c.username == username)).first()
    scrypt.decrypt(hashed, password, maxtime=0.5)
    return make_login_token(username)

@post('/login.html')
def form_login():
    username = request.forms["username"]
    password = request.forms["password"]

    response.set_cookie("token", login(username, password))

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
    except scrypt.error:
        return {"error": "invalid password"}

run(host='localhost', port=8080)
