import re

from . import ast
from .util import flatten
from .issue import Issue, IssueLocation


WHITESPACE_INDENT_RE = re.compile('^\s*')
INDENT_RE = re.compile('^ *')


def get_line_beginning(source, node):
    source = source[:node.begin.index]
    return source.split('\n')[-1]


def get_indent_level(source, node):
    beginning = get_line_beginning(source, node)
    if beginning and not beginning.isspace():
        return None
    return len(beginning)


def contains_exclusively(string, char):
    return string.replace(char, '') == ''


def check_indentation(file, config):
    indent_size = config.get('indent_size', 4)

    issues = []

    def add_issue(location, msg):
        issues.append(Issue.from_ast(file, location, msg))

    def check_indent(expected_level, node, inline=False):
        node_level = get_indent_level(file.source, node)
        if node_level is None:
            if not inline and not isinstance(node, ast.Jinja):
                add_issue(node.begin, 'Should be on the next line')
            return

        if node_level != expected_level:
            msg = 'Bad indentation, expected {}, got {}'.format(
                expected_level, node_level,
            )
            add_issue(node.begin, msg)

    def check_attribute(expected_level, attr, inline=False):
        if not attr.value:
            return

        if attr.begin.line != attr.value.begin.line:
            add_issue(
                attr.begin,
                'The value must begin on line {}'.format(attr.begin.line),
            )
        check_content(expected_level, attr.value, inline=True)

    def check_opening_tag(expected_level, tag, inline=False):
        if len(tag.attributes) and tag.begin.line != tag.end.line:
            first = tag.attributes[0]
            check_node(
                expected_level + indent_size,
                first,
                inline=isinstance(first, ast.Attribute),
            )
            attr_level = len(get_line_beginning(file.source, first))
            for attr in tag.attributes[1:]:
                # attr may be a JinjaElement
                check_node(
                    expected_level if inline else attr_level,
                    attr,
                    inline=isinstance(attr, ast.Attribute),
                )

    def check_comment(expected_level, tag, inline=False):
        pass

    def check_jinja_comment(expected_level, tag, inline=False):
        pass

    def check_jinja_tag(expected_level, tag, inline=False):
        pass

    def check_string(expected_level, string, inline=False):
        if string.value.begin.line != string.value.end.line:
            inline = False
        check_content(string.value.begin.column, string.value, inline=inline)

    def check_integer(expected_level, integer, inline=False):
        pass

    def get_first_child_node(parent):
        for c in parent:
            if isinstance(c, ast.Node):
                return c
        return None

    def has_jinja_element_child(parent, tag_name):
        child = get_first_child_node(parent)
        return (
            isinstance(child, ast.JinjaElement) and
            child.parts[0].tag.name == tag_name
        )

    def check_jinja_element_part(expected_level, part, inline=False):
        check_node(expected_level, part.tag, inline=inline)
        element_names_to_not_indent = (
            config.get('jinja_element_names_to_not_indent', [])
        )
        do_not_indent = part.tag.name in element_names_to_not_indent and \
            has_jinja_element_child(part.content, part.tag.name)
        shift = 0 if inline or do_not_indent else indent_size
        content_level = expected_level + shift
        if part.content is not None:
            check_content(content_level, part.content, inline=inline)

    def check_jinja_element(expected_level, element, inline=False):
        if element.begin.line == element.end.line:
            inline = True
        for part in element.parts:
            check_node(expected_level, part, inline=inline)
        if element.closing_tag is not None:
            check_indent(expected_level, element.closing_tag, inline=inline)

    def check_jinja_variable(expected_level, var, inline=False):
        pass

    def check_element(expected_level, element, inline=False):
        opening_tag = element.opening_tag
        closing_tag = element.closing_tag
        check_opening_tag(expected_level, opening_tag, inline=inline)
        if closing_tag:
            if inline or opening_tag.end.line == closing_tag.begin.line:
                check_content(expected_level, element.content, inline=True)
            else:
                check_content(
                    expected_level + indent_size,
                    element.content,
                )
                check_indent(expected_level, closing_tag)

    def check_node(expected_level, node, inline=False):
        check_indent(expected_level, node, inline=inline)

        types_to_functions = {
            ast.Attribute: check_attribute,
            ast.Comment: check_comment,
            ast.Element: check_element,
            ast.Integer: check_integer,
            ast.JinjaComment: check_jinja_comment,
            ast.JinjaElement: check_jinja_element,
            ast.JinjaElementPart: check_jinja_element_part,
            ast.JinjaTag: check_jinja_tag,
            ast.JinjaVariable: check_jinja_variable,
            ast.String: check_string,
        }

        func = types_to_functions.get(type(node))
        if func is None:
            raise Exception('Unexpected {!r} node at {}'.format(
                type(node), node.begin,
            ))

        func(expected_level, node, inline=inline)

    def check_content_str(expected_level, string, parent_node):
        lines = string.split('\n')
        expected_indent = expected_level * ' '

        indent = INDENT_RE.match(lines[0]).group(0)

        if len(indent) > 1:
            msg = (
                'Expected at most one space at the beginning of the text '
                'node, got {} spaces'
            ).format(len(indent))
            add_issue(parent_node.begin, msg)

        # skip the first line since there is certainly an HTML tag before
        for line in lines[1:]:
            if line.strip() == '':
                continue
            indent = INDENT_RE.match(line).group(0)
            if indent != expected_indent:
                msg = 'Bad text indentation, expected {}, got {}'.format(
                    expected_level, len(indent),
                )
                add_issue(parent_node.begin, msg)

    def check_content(expected_level, parent_node, inline=False):
        inline_parent = inline
        for i, child in enumerate(parent_node):
            next_child = get_first_child_node(parent_node[i + 1:])

            if isinstance(child, str):
                check_content_str(expected_level, child, parent_node)
                if not child.strip(' '):
                    inline = True
                elif child.strip() and child.count('\n') <= 1:
                    inline = True
                elif (next_child and
                      child.strip() and
                      not child.replace(' ', '').endswith('\n')):
                    inline = True
                elif child.replace(' ', '').endswith('\n\n'):
                    inline = False
                if inline_parent and not inline:
                    msg = (
                        'An inline parent element must only contain '
                        'inline children'
                    )
                    add_issue(parent_node.begin, msg)
                continue

            if isinstance(child, ast.Node):
                if next_child and child.begin.line == next_child.end.line:
                    inline = True
                check_node(expected_level, child, inline=inline)
                continue

            raise Exception()

    check_content(0, file.tree)

    return issues


def check_space_only_indent(file, _config):
    issues = []
    for i, line in enumerate(file.lines):
        indent = WHITESPACE_INDENT_RE.match(line).group(0)
        if not contains_exclusively(indent, ' '):
            loc = IssueLocation(
                file_path=file.path,
                line=i,
                column=0,
            )
            issue = Issue(loc, 'Should be indented with spaces')
            issues.append(issue)
    return issues


checks = [
    check_space_only_indent,
    check_indentation,
]


def check_file(file, config):
    return set(flatten(check(file, config) for check in checks))


def check_files(files, config):
    return flatten(check_file(file, config) for file in files)
