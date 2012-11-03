#!/usr/bin/env python3

import hmac
import os.path as path
from base64 import b64encode
from hashlib import sha256
from os import urandom

import pygit2 as git
import scrypt
import sqlalchemy as sql
from bottle import get, post, request, run, static_file
from docutils.core import publish_file

engine = sql.create_engine("sqlite:///wiki.sqlite3", echo=True)
metadata = sql.MetaData()
users = sql.Table("users", metadata,
                  sql.Column("username", sql.String, primary_key = True),
                  sql.Column("email", sql.String, nullable = False),
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

@get('/page/<filename>.rst')
def page(filename):
    return static_file(filename + '.rst', root="repo",
                       mimetype="text/x-rst; charset=UTF-8")

@get('/page/<filename>.html')
def html_page(filename):
    return static_file(filename + '.html', root="generated")

@get('/log.json')
def log():
    commits = repo.walk(repo.head.oid, git.GIT_SORT_TIME)

    try:
        page = request.query["page"] + ".rst"
        commits = filter(lambda c: page in c.tree, commits)
    except KeyError:
        pass

    return {"log": [{"message": c.message,
                     "author": c.author.name}
                    for c in commits]}

@post('/update/json/<filename>')
def update(filename):
    message, page, token = request.json["message"], request.json["page"], request.json["token"]

    username = check_login_token(token)

    if username is None:
        return {"error": "invalid login token"}

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

@post('/register.json')
def register():
    email, username, password = request.json["email"], request.json["username"], request.json["password"]
    hashed = scrypt.encrypt(b64encode(urandom(64)), password, maxtime=0.5)
    connection.execute(users.insert().values(username=username,
                                             email=email,
                                             password_hash=hashed))
    return {"token": make_login_token(username)}

@post('/login.json')
def login():
    username, password = request.json["username"], request.json["password"]
    hashed, = connection.execute(sql.select([users.c.password_hash],
                                            users.c.username == username)).first()
    scrypt.decrypt(hashed, password, maxtime=0.5)
    return {"token": make_login_token(username)}

run(host='localhost', port=8080)
