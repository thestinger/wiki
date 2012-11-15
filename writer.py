#!/usr/bin/env python3

from docutils.writers import html4css1

class HTMLTranslator(html4css1.HTMLTranslator):
    doctype = "<!doctype html>\n"
    doctype_mathml = doctype

class Writer(html4css1.Writer):
    def __init__(self):
        super().__init__()
        self.translator_class = HTMLTranslator
