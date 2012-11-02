#!/usr/bin/env python3

import os.path as path

import pygit2 as git
from bottle import route, run, static_file
from docutils.core import publish_file

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

@route('/page/<filename>.rst')
def page(filename):
    return static_file(filename + '.rst', root="repo")

@route('/page/<filename>.html')
def html_page(filename):
    return static_file(filename + '.html', root="generated")

@route('/log.json')
def log():
    return {"log": [c.message for c in repo.walk(repo.head.oid, git.GIT_SORT_TIME)]}

run(host='localhost', port=8080)
