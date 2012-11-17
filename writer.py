#!/usr/bin/env python3

from docutils.writers import html4css1

class HTMLTranslator(html4css1.HTMLTranslator):
    doctype = "<!DOCTYPE html>\n"
    doctype_mathml = doctype
    content_type = '<meta charset="%s"/>\n'
    content_type_mathml = content_type

class Writer(html4css1.Writer):
    def __init__(self):
        super().__init__()
        self.translator_class = HTMLTranslator
