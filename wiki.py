#!/usr/bin/env python3

import pygit2 as git
from bottle import route, run, static_file

repo = git.init_repository("repo", False)

if 'refs/heads/master' not in repo.listall_references():
    author = git.Signature('wiki', 'danielmicay@gmail.com')
    tree = repo.TreeBuilder().write()
    repo.create_commit('refs/heads/master', author, author,
                       'initialize repository', tree, [])

@route('/page/<filename>.rst')
def page(filename):
    return static_file(filename + '.rst', root="repo")

@route('/page/<filename>.html')
def html_page(filename):
    return static_file(filename + '.html', root="generated")

run(host='localhost', port=8080)
