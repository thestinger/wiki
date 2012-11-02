#!/usr/bin/env python3

import pygit2 as git
from bottle import run

repo = git.init_repository("repo", False)

if 'refs/heads/master' not in repo.listall_references():
    author = git.Signature('wiki', 'danielmicay@gmail.com')
    tree = repo.TreeBuilder().write()
    repo.create_commit('refs/heads/master', author, author,
                       'initialize repository', tree, [])

run(host='localhost', port=8080)
