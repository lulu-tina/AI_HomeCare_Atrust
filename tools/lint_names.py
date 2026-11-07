"""Poor-man's pyflakes: report names that are read but never bound in any enclosing scope, and
function-local names that shadow a module-level name and are read before their first assignment.
Usage: python tools/lint_names.py app.py"""
import ast, builtins, sys

class Scope:
    def __init__(self, node, parent, kind):
        self.node, self.parent, self.kind = node, parent, kind
        self.bound, self.loads, self.declared_global = set(), [], set()
        self.first_store = {}

def bind_target(scope, target):
    for n in ast.walk(target):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            scope.bound.add(n.id); scope.first_store.setdefault(n.id, n.lineno)

def build(tree):
    module = Scope(tree, None, 'module'); scopes = [module]
    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                if not isinstance(child, ast.Lambda):
                    scope.bound.add(child.name); scope.first_store.setdefault(child.name, child.lineno)
                    for d in child.decorator_list: visit_expr(d, scope)
                s = Scope(child, scope, 'function'); scopes.append(s)
                a = child.args
                for arg in a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else []):
                    s.bound.add(arg.arg)
                for d in a.defaults + [x for x in a.kw_defaults if x]: visit_expr(d, scope)
                body = child.body if isinstance(child.body, list) else [child.body]
                for b in body:
                    visit_stmt(b, s)
            elif isinstance(child, ast.ClassDef):
                scope.bound.add(child.name)
                s = Scope(child, scope, 'class'); scopes.append(s)
                for b in child.body: visit_stmt(b, s)
            else:
                visit_stmt(child, scope) if isinstance(child, ast.stmt) else visit_expr(child, scope)
    def visit_stmt(node, scope):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            visit(ast.Module(body=[node], type_ignores=[]), scope); return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for al in node.names:
                name = (al.asname or al.name).split('.')[0]
                scope.bound.add(name); scope.first_store.setdefault(name, node.lineno)
            return
        if isinstance(node, ast.Global):
            scope.declared_global.update(node.names); scope.bound.update(node.names); return
        if isinstance(node, ast.Nonlocal):
            scope.bound.update(node.names); return
        if isinstance(node, ast.ExceptHandler) and node.name:
            scope.bound.add(node.name)
        for n in ast.iter_child_nodes(node):
            if isinstance(n, ast.stmt): visit_stmt(n, scope)
            elif isinstance(n, ast.ExceptHandler):
                if n.name: scope.bound.add(n.name); scope.first_store.setdefault(n.name, n.lineno)
                if n.type: visit_expr(n.type, scope)
                for b in n.body: visit_stmt(b, scope)
            elif isinstance(n, ast.match_case if hasattr(ast,'match_case') else ()):
                for b in n.body: visit_stmt(b, scope)
            else: visit_expr(n, scope)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)):
            pass
    def visit_expr(node, scope):
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            s = Scope(node, scope, 'comp'); scopes.append(s)
            for gen in node.generators:
                bind_target(s, gen.target); visit_expr(gen.iter, scope if gen is node.generators[0] else s)
                for cond in gen.ifs: visit_expr(cond, s)
            elts = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
            for e in elts: visit_expr(e, s)
            return
        if isinstance(node, ast.Lambda):
            s = Scope(node, scope, 'function'); scopes.append(s)
            a = node.args
            for arg in a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else []): s.bound.add(arg.arg)
            for d in a.defaults + [x for x in a.kw_defaults if x]: visit_expr(d, scope)
            visit_expr(node.body, s); return
        if isinstance(node, ast.NamedExpr):
            scope.bound.add(node.target.id); scope.first_store.setdefault(node.target.id, node.lineno)
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load): scope.loads.append((node.id, node.lineno))
            else: scope.bound.add(node.id); scope.first_store.setdefault(node.id, node.lineno)
            return
        for n in ast.iter_child_nodes(node): visit_expr(n, scope)
    for stmt in tree.body: visit_stmt(stmt, module)
    return scopes

def resolve(name, scope):
    s = scope
    while s is not None:
        if s.kind != 'class' or s is scope:
            if name in s.bound: return s
        s = s.parent
    return None

def main(path):
    tree = ast.parse(open(path, encoding='utf-8').read())
    scopes = build(tree); module = scopes[0]
    problems = 0
    for s in scopes:
        for name, line in s.loads:
            if resolve(name, s) is None and not hasattr(builtins, name) and name not in ('__file__', '__name__'):
                print(f'{path}:{line}: undefined name {name!r}'); problems += 1
            elif s.kind == 'function' and name in s.bound and name in module.bound:
                if line < s.first_store.get(name, 10**9) and name not in s.parent.bound if s.parent else False:
                    pass
        if s.kind == 'function':
            for name in s.bound:
                if name in module.bound and name not in s.declared_global and isinstance(s.node, (ast.FunctionDef,)):
                    first = s.first_store.get(name)
                    early = [l for n, l in s.loads if n == name and first and l < first]
                    if early:
                        print(f'{path}:{early[0]}: {name!r} read before assignment in {s.node.name}() (also a module-level name)'); problems += 1
    print('done,', problems, 'problem(s)')
    return problems

if __name__ == '__main__':
    sys.exit(1 if main(sys.argv[1]) else 0)
