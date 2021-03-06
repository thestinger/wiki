#!/usr/bin/env python3

from datetime import datetime
from hmac import compare_digest
from os import path, statvfs, urandom
from subprocess import Popen, PIPE
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

import pygit2 as git
import scrypt
import sqlalchemy as sql
from bottle import app, get, post, redirect, response, request, run, static_file, template, view
from docutils.core import publish_string
from lxml.html.diff import htmldiff
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import DiffLexer

from sign import check_token, make_token
from writer import HTMLTranslator, Writer

class Error(Exception): pass

engine = sql.create_engine("sqlite:///wiki.sqlite3",
                           connect_args={'check_same_thread': False},
                           poolclass=sql.pool.QueuePool)
metadata = sql.MetaData()
metadata.bind = engine

users = sql.Table("users", metadata,
                  sql.Column("username", sql.String, primary_key = True),
                  sql.Column("email", sql.String, nullable = False),
                  sql.Column("email_verified", sql.Boolean, nullable = False,
                             default = False),
                  sql.Column("password_hash", sql.Binary, nullable = False),
                  sql.Column("password_salt", sql.Binary, nullable = False))

generated = sql.Table("generated", metadata,
                      sql.Column("title", sql.String, primary_key = True),
                      sql.Column("revision", sql.String, primary_key = True),
                      sql.Column("navigation", sql.Boolean, primary_key = True),
                      sql.Column("content", sql.String, nullable = False))

metadata.create_all(engine)

engine.execute('CREATE VIRTUAL TABLE IF NOT EXISTS corpus USING fts4(title, page)')
corpus = sql.Table("corpus", metadata, autoload=True)

repo = git.init_repository("repo", True)

try:
    with open("key.rnd", "rb") as f:
        KEY = f.read()
except FileNotFoundError:
    with open("key.rnd", "wb") as f:
        KEY = urandom(256)
        f.write(KEY)

def login_redirect():
    redirect('/login.html?' + urlencode({"url": request.url}))

def validate_login_cookie():
    token = request.get_cookie("token")
    if token is None:
        login_redirect()

    username = check_token(KEY, token)
    if username is None:
        login_redirect()

    return username

def get_page_revision(name, revision):
    return repo[repo[revision].tree[name + ".rst"].oid].data

def render_html(name, source, translator_class=None):
    writer = Writer()
    if translator_class is not None:
        writer.translator_class = translator_class

    settings = {"stylesheet_path": "/static/html4css1.css,/static/main.css",
                "embed_stylesheet": False,
                "file_insertion_enabled": False,
                "raw_enabled": False,
                "xml_declaration": False}

    return publish_string(source, writer_name="html", writer=writer,
                          settings_overrides=settings).decode()

def get_html_revision(title, revision, navigation):
    with engine.connect() as connection:
        s = sql.select([generated.c.content],
                       (generated.c.title == title) &
                       (generated.c.revision == revision) &
                       (generated.c.navigation == navigation))
        content = connection.execute(s).scalar()
        if content is None:
            class NavigationHTMLTranslator(HTMLTranslator):
                def __init__(self, document):
                    super().__init__(document)
                    self.body_prefix = [template("body_prefix.html", name=title)]

            content = render_html(title, get_page_revision(title, revision),
                                  NavigationHTMLTranslator if navigation else None)
            connection.execute(generated.insert().values(title=title,
                                                         revision=revision,
                                                         navigation=navigation,
                                                         content=content))
        return content

@get('/')
@view('index.html')
def index():
    return {}

def list_pages():
    return {"pages": [p.name[:-4] for p in repo.head.tree]}

@get('/list.html')
@view('list.html')
def html_list_pages():
    return list_pages()

@get('/list.json')
def json_list_pages():
    return list_pages()

@get('/page/<title>.rst')
def rst_page(title):
    response.content_type = "text/x-rst; charset=UTF-8"
    revision = request.query.get("revision", repo.head.hex)
    return get_page_revision(title, revision)

@get('/page/<title>.html')
def html_page(title):
    revision = request.query.get("revision", repo.head.hex)
    return get_html_revision(title, revision, False)

@get('/nav/<title>.html')
def nav_page(title):
    revision = request.query.get("revision", repo.head.hex)
    return get_html_revision(title, revision, True)

@get('/static/<name>.css')
def css(name):
    return static_file(name + ".css", root="static")

def search():
    query = request.query["query"]
    result = engine.execute("select title from corpus where corpus match ?", (query,))
    return {"matches": [name for name, in result]}

@get('/search.html')
@view('search.html')
def html_search():
    return search()

@get('/search.json')
def json_search():
    return search()

def page_log(page, commits):
    # TODO: currently ignores the possibility of moved files
    # TODO: does not consider removed/recreated files
    for commit in commits:
        tree = commit.tree
        if page not in tree:
            continue
        parent_tree = commit.parents[0].tree

        diff = parent_tree.diff(tree)
        files = next(iter(diff)).new_file_path
        if any(x[0] == page for x in files):
            yield commit

def get_current_name(tree, patches):
    if len(patches) == 2: # move
        a, b = patches
        return a.new_file_path if a.new_file_path in tree else b.old_file_path
    assert len(patches) == 1 # commits are a move, or an edit
    return patches[0].new_file_path

def get_changed(commit):
    tree = commit.tree
    parent_tree = commit.parents[0].tree
    diff = parent_tree.diff(tree)
    patches = list(diff)
    return get_current_name(tree, patches)

def log():
    commits = list(repo.walk(repo.head.oid, git.GIT_SORT_TIME))[:-1]

    page = request.query.get("page")
    if page is not None:
        commits = page_log(page + ".rst", commits)

    return [{"message": c.message,
             "author": c.author.name,
             "time": datetime.fromtimestamp(c.author.time).isoformat() + "Z",
             "revision": c.hex,
             "page": page if page is not None else get_changed(c)[:-4]}
            for c in commits]

@get('/log.html')
@view('log.html')
def html_log():
    return {"log": log(), "name": request.query.get("page")}

@get('/log.json')
def json_log():
    return {"log": log()}

@get('/edit/html/<title>')
@view("edit.html")
def html_edit(title):
    username = validate_login_cookie()
    form_token = make_token(KEY, username + "-edit")

    try:
        blob = get_page_revision(title, repo.head.oid)
    except KeyError: # title.rst not in tree
        blob = ""

    return dict(content=blob, name=title, token=form_token)

def commit(username, message, tree):
    email = engine.execute(sql.select([users.c.email],
                                      users.c.username == username)).scalar()
    signature = git.Signature(username, email)
    repo.create_commit('refs/heads/master', signature, signature, message,
                       tree, [repo.head.oid])

def edit(title, message, page, username):
    # verify that the source is valid
    render_html(title, page)

    oid = repo.write(git.GIT_OBJ_BLOB, page)
    bld = repo.TreeBuilder(repo.head.tree)
    bld.insert(title + '.rst', oid, 0o100644)
    tree = bld.write()
    commit(username, message, tree)

    with engine.connect() as c:
        c.execute(corpus.delete().where(corpus.c.title == title))
        c.execute(corpus.insert().values(title=title, page=page))

def is_changed(name, content):
    filename = name + '.rst'

    if filename not in repo.head.tree:
        return True

    return content != get_page_revision(name, repo.head.oid).decode()

@post('/edit/html/<title>')
def form_edit(title):
    action = request.forms["action"]
    message = request.forms["message"]
    page = request.forms["page"]
    form_token = request.forms["token"]
    token = request.get_cookie("token")

    if action == "Preview":
        class PreviewHTMLTranslator(HTMLTranslator):
            def __init__(self, document):
                super().__init__(document)
                self.body_prefix = [template("body_prefix.html", name=title)]
                self.body_suffix = [template("edit_suffix.html", content=page, token=form_token)]
        return render_html(title, page, PreviewHTMLTranslator)

    username = check_token(KEY, token)

    if check_token(KEY, form_token) != username + "-edit":
        return

    if not is_changed(title, page):
        redirect(request.url)

    edit(title, message, page, username)

    redirect('/nav/{}.html'.format(title))

@post('/edit/json/<title>')
def json_edit(title):
    message = request.json["message"]
    page = request.json["page"]
    token = request.json["token"]

    if not is_changed(title, page):
        return {"error": "an edit must make changes"}

    username = check_token(KEY, token)
    if username is None:
        return {"error": "invalid login token"}

    edit(title, message, page, username)

@get('/<title>/move.html')
@view('move.html')
def html_move(title):
    username = validate_login_cookie()
    form_token = make_token(KEY, username + "-move")
    return dict(title=title, token=form_token)

def move(title, new_title, username):
    blob = repo.head.tree[title + ".rst"].oid
    bld = repo.TreeBuilder(repo.head.tree)
    oid = bld.remove(title + '.rst')
    bld.insert(new_title + '.rst', blob, 100644)
    tree = bld.write()

    message = "renamed: {} -> {}".format(title, new_title)
    commit(username, message, tree)

@post('/<title>/move.html')
def form_move(title):
    form_token = request.forms["token"]
    destination = request.forms["destination"]
    token = request.get_cookie("token")

    username = check_token(KEY, token)

    if check_token(KEY, form_token) != username + "-move":
        return

    move(title, destination, username)

    redirect('/nav/' + destination + '.html')

@post('/<title>/<new_title>/move.json')
def json_move(title, new_title):
    token = request.json["token"]

    username = check_token(KEY, token)
    if username is None:
        return {"error": "invalid login token"}

    move(title, new_title, username)

@get('/<revision>/revert.html')
@view("revert.html")
def html_revert(revision):
    username = validate_login_cookie()
    form_token = make_token(KEY, username + "-revert")

    return dict(token=form_token)

def get_patch(revision):
    target = repo[revision]
    tree = target.tree
    parent_tree = target.parents[0].tree
    return parent_tree.diff(tree).patch

@get('/<revision>/diff.html')
@view('diff.html')
def html_diff(revision):
    patch = get_patch(revision)
    return {"patch": highlight(patch, DiffLexer(), HtmlFormatter())}

@get('/<revision>/visual_diff.html')
@view('diff.html')
def visual_diff(revision):
    target = repo[revision]
    parent = target.parents[0]

    tree = target.tree
    parent_tree = target.parents[0].tree

    diff = parent_tree.diff(tree)
    patches = list(diff)
    filename = get_current_name(tree, patches)
    name = filename[:-4]
    target_html = get_html_revision(name, revision, False)

    if len(patches) == 2:
        return {"patch": htmldiff(target_html, target_html)}

    parent_html = (get_html_revision(name, parent.hex, False)
                   if filename in parent_tree else "")

    return {"patch": htmldiff(parent_html, target_html)}

@get('/<revision>/diff.json')
def json_diff(revision):
    return {"patch": get_patch(revision)}

def revert(username, target):
    tree = target.tree
    parent_tree = target.parents[0].tree

    diff = parent_tree.diff(tree)

    filename = next(iter(diff)).new_file_path
    name = filename[:-4]

    current = get_page_revision(name, repo.head.hex)

    with TemporaryDirectory() as tmp:
        with open(path.join(tmp, filename), "wb") as f:
            f.write(current)

        with Popen(["patch", "-Rfd", tmp, "-o", "-"], stdin=PIPE, stdout=PIPE) as p:
            result, _ = p.communicate(diff.patch)

    if current == result:
        raise Error("an edit must make changes")

    edit(name, 'Revert "{}"'.format(target.message.split("\n", 1)[0]), result, username)

    return name

@post('/<revision>/revert.html')
def form_revert(revision):
    target = repo[revision]
    form_token = request.forms["token"]
    token = request.get_cookie("token")

    username = check_token(KEY, token)

    if check_token(KEY, form_token) != username + "-revert":
        return

    name = revert(username, target)
    redirect("/nav/{}.html".format(name))

@post('/<revision>/revert.json')
def json_revert(revision):
    target = repo[revision]
    token = request.json["token"]

    username = check_token(KEY, token)
    if username is None:
        return {"error": "invalid login token"}

    try:
        revert(username, target)
    except Error as e:
        return {"error": e.args[0]}

@get('/register.html')
@view('register.html')
def html_register():
    return {}

def register(username, password, email):
    salt = urandom(64)
    hashed = scrypt.hash(password, salt)
    engine.execute(users.insert().values(username=username,
                                         email=email,
                                         password_hash=hashed,
                                         password_salt=salt))

@post('/register.html')
def form_register():
    email = request.forms["email"]
    password = request.forms["password"]
    username = request.forms["username"]

    register(username, password, email)

    response.set_cookie("token", make_token(KEY, username), httponly=True)
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

    return {"token": make_token(KEY, username)}

@get('/login.html')
@view('login.html')
def html_login():
    return {"url": request.query.get("url", "/")}

def login(username, password):
    select = sql.select([users.c.password_hash, users.c.password_salt],
                        users.c.username == username)
    hashed, salt = engine.execute(select).first()

    if not compare_digest(hashed, scrypt.hash(password, salt)):
        raise ValueError("invalid password")

    return make_token(KEY, username)

@post('/login.html')
def form_login():
    username = request.forms["username"]
    password = request.forms["password"]

    response.set_cookie("token", login(username, password), httponly=True)
    redirect(request.forms.get("url", "/"))

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
    bsize = statvfs('.').f_bsize
    page_size = engine.execute("PRAGMA page_size").scalar()

    if page_size != bsize:
        engine.execute("PRAGMA page_size = " + str(bsize))
        engine.execute("VACUUM")

    if 'refs/heads/master' not in repo.listall_references():
        author = git.Signature('wiki', 'danielmicay@gmail.com')
        tree = repo.TreeBuilder().write()
        repo.create_commit('refs/heads/master', author, author,
                           'initialize repository', tree, [])

main()

if __name__ == '__main__':
    run(host='localhost', port=8080)
else:
    application = app()
