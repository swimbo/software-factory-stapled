"""Audit executed Python snippets; quoted heredoc data is not execution.

This is an audit of the recorded development commands, not a general shell
security policy or a proof of dynamic Python behavior.
"""

def diagnostic_imports(command, dependency):
    import ast
    import re
    import shlex
    lines=command.splitlines();shell=[];snippets=[];index=0
    while index<len(lines):
        line=lines[index];match=re.search(r"<<-?\s*(['\"]?)([A-Za-z0-9_]+)\1",line)
        if match:
            head=line[:match.start()];delimiter=match.group(2);body=[];index+=1
            while index<len(lines) and lines[index].strip()!=delimiter:
                body.append(lines[index]);index+=1
            if any(re.fullmatch(r'(?:.*/)?python(?:3(?:\.\d+)?)?',t) for t in shlex.split(head)):
                snippets.append('\n'.join(body))
            shell.append(head)
        else:shell.append(line)
        index+=1
    lex=shlex.shlex('\n'.join(shell),posix=True,punctuation_chars=';&|');lex.whitespace_split=True
    tokens=list(lex)
    for index,token in enumerate(tokens):
        if not re.fullmatch(r'(?:.*/)?python(?:3(?:\.\d+)?)?',token):continue
        args=[]
        for arg in tokens[index+1:]:
            if arg in (';','&&','||','|','&'):break
            args.append(arg)
        if '-c' in args and args.index('-c')+1<len(args):snippets.append(args[args.index('-c')+1])
    count=0
    for snippet in snippets:
        tree=ast.parse(snippet)
        for node in ast.walk(tree):
            if isinstance(node,ast.Import) and any(n.name==dependency for n in node.names):count+=1
            elif isinstance(node,ast.ImportFrom) and node.module==dependency:count+=1
            elif isinstance(node,ast.Call) and node.args and isinstance(node.args[0],ast.Constant) and node.args[0].value==dependency:
                func=node.func
                if isinstance(func,ast.Name) and func.id=='__import__' or isinstance(func,ast.Attribute) and func.attr=='import_module':count+=1
    return count


def diagnostic_error(observation, dependency):
    """Recognize explicit failed terminal/capture evidence, never exit0/text alone."""
    import json
    if not isinstance(observation, dict) or observation.get('timeout') is True:
        return False
    text = ''.join(p.get('text', '') for p in observation.get('content', []) if p.get('type') == 'text')
    if 'ModuleNotFoundError' not in text or dependency not in text:
        return False
    code = observation.get('exit_code')
    if type(code) is int:
        return code not in (0, -1)
    try:
        capture = json.loads(text)
    except (ValueError, TypeError):
        return False
    return (isinstance(capture, dict) and capture.get('capture_complete') is True
            and type(capture.get('exit_code')) is int and capture['exit_code'] not in (0, -1))
