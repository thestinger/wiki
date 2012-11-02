#!/usr/bin/env python3

import hmac
import os.path as path
from base64 import b64encode
from hashlib import sha256
from os import urandom

import pygit2 as git
import scrypt
import sqlalchemy as sql
from bottle import request, route, run, static_file
from docutils.core import publish_file

engine = sql.create_engine("sqlite:///wiki.sqlite3", echo=True)
metadata = sql.MetaData()
users = sql.Table("users", metadata,
                  sql.Column("username", sql.String, primary_key = True),
                  sql.Column("password_hash", sql.Binary, nullable = False))
metadata.create_all(engine)

connection = engine.connect()

repo = git.init_repository("repo", False)

author = git.Signature('wiki', 'danielmicay@gmail.com')

if 'refs/heads/master' not in repo.listall_references():
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

# TODO: these should also include a timestamp, and expire eventually

def make_login_token(username):
    return "|".join((generate_mac(username), username))

def check_login_token(token):
    "Return the username if the token is valid, otherwise None."
    mac, username = token.split('|', 1)
    if hmac.compare_digest(mac, generate_mac(username)):
        return username

@route('/page/<filename>.rst')
def page(filename):
    return static_file(filename + '.rst', root="repo")

@route('/page/<filename>.html')
def html_page(filename):
    return static_file(filename + '.html', root="generated")

@route('/log.json')
def log():
    return {"log": [c.message for c in repo.walk(repo.head.oid, git.GIT_SORT_TIME)]}

@route('/update/json/<filename>', method='POST')
def update(filename):
    if check_login_token(request.json["token"]) is None:
        return {"error": "invalid login token"}

    with open(path.join("repo", filename + '.rst'), "w") as f:
        f.write(request.json["page"])
    generate_html_page(filename)

    oid = repo.write(git.GIT_OBJ_BLOB, request.json["page"])
    bld = repo.TreeBuilder()
    bld.insert(filename + '.rst', oid, 100644)
    tree = bld.write()
    repo.create_commit('refs/heads/master', author, author, 'update', tree, [repo.head.oid])

@route('/register/json/', method='POST')
def register():
    username, password = request.json["username"], request.json["password"]
    hashed = scrypt.encrypt(b64encode(urandom(64)), request.json["password"],
                            maxtime=0.5)
    connection.execute(users.insert().values(username=username,
                                             password_hash=hashed))

@route('/login/json/', method='POST')
def login():
    username, password = request.json["username"], request.json["password"]
    hashed, = connection.execute(sql.select([users.c.password_hash],
                                            users.c.username == username)).first()
    scrypt.decrypt(hashed, password, maxtime=0.5)
    return {"token": make_login_token(username)}

run(host='localhost', port=8080)
