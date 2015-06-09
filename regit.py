#!/usr/bin/env python

import argparse
import json
import os
import pickle
import re
import subprocess
import sys
import tempfile

topdir = None

re_branch = re.compile(r"  (?P<name>.*)")
re_current = re.compile(r"\* (?P<name>.*)")
re_commit_oneline = re.compile(r"(?P<hash>[a-f0-9]{40,40}) (?P<descr>.*)")


class Branch(object):
    map = {}
    list = []
    current = None

    def switch(branch, base=None):
        if branch==Branch.current:
            return

#        print("regit: switching to branch %s." % branch)

        if base:
            git_command(['checkout', '-B', branch.name, base.name], True)
        else:
            git_command(['checkout', branch.name, '--'], True)

        Branch.current = branch

    def datafile():
        return os.path.join(topdir, ".regit")

    def maybe_new(name):
        existing = Branch.map.get(name)
        if existing:
            return existing

        return Branch(name)

    def __init__(s, name):
        s.name = name
        s.base = None
        s.deps = None
        s.rebase_tip = None
        s.updated = False
        s.have_data = False
        Branch.map[name] = s

    def __str__(s):
        return s.name

    def head(s):
        return git_command_output(['show-ref', '-s', "refs/heads/%s" % s]).rstrip()

    def merge_base(s, other):
        return git_command_output(['merge-base', other.name, s.name]).rstrip()

    def based_on(s, other):
        return s.merge_base(other) == other.head()

    def dependency_commit(s):
        x = "DEPENDENCY UPDATE"
        out = git_command_output(["log", "--pretty=oneline", "--grep=^%s$" % x])
        out = out.splitlines()
        if not out:
            return None
        if len(out)>1:
            print("regit: warning: multiple dependency commits.\n")
            return None
        m = re_commit_oneline.match(out[0])
        if not m:
            return None

        return m.group("hash")

    def get(have_state=False):
        branches = {}
        out = git_command_output(['branch'])
        for line in out.splitlines():
            line = line.rstrip()

            m = re_branch.match(line)
            if m:
                Branch(m.group("name"))
                continue

            m = re_current.match(line)
            if m:
                if (not have_state) and m.group("name").startswith("regit/"):
                    _err("regit: cannot work on regit/* branches. Exiting.")
                Branch.current = Branch(m.group("name"))
                continue
        Branch.list = [value for (key, value) in sorted(Branch.map.items())]

    def has_branchfile(s):
        return os.path.isfile(s.branch_file())

    def get_data(s):
        if s.have_data:
            return

        if not s.has_branchfile():
            return False

        Branch.switch(s)

        f = open(s.branch_file(), 'r')
        bdict = json.load(f)
        base = bdict.get('base')
        if not base:
            _err("regit: branch dependency file has no base branch! exiting.")
        s.base = Branch.map.get(base)
        if not s.base:
            _err("regit: branch \"%s\" has unknown base branch \"%s\". exiting." %(s.name, base))

        rebase_tip = bdict.get('rebase_tip')
        if not rebase_tip:
            _err("regit: branch \"%s\" has unset rebase tip. exiting." %(s.name))
        s.rebase_tip = rebase_tip

        _deps = bdict.get('deps')
        deps = []
        if _deps:
            for branch in _deps:
                if branch==base:
                    print("regit: warning: branch \"%s\" depends on it's base branch." %s)
                elif branch==s.name:
                    print("regit: warning: branch \"%s\" depends on itself." % s)
                elif branch in deps:
                    print("regit: warning: branch \"%s\" has duplicate "
                            "dependency \"%s\"." % (s, branch))
                else:
                    deps.append(branch)

        s.deps = []
        for dep in deps:
            b = Branch.map.get(dep)
            if not b:
                print("regit: error: branch \"%s\" depends on "
                        "unknown branch \"%s\"." % (s.name, branch))
            else:
                s.deps.append(b)
        s.have_data=True

    def update(s, _continue=False):
        if s.updated:
            print("regit: skipping already updated branch")
            return

        s.get_data()
        deps = s.deps or []
        already_done = []

        if (not s.base) and (not s.deps):
            print("regit: no dependency information for branch \"%s\"." % s)
            s.updated=True
            return

        tmp = None
        if deps:
            if _continue:
                tmp = Branch.maybe_new("regit/base/%s" % s)
            else:
                tmp = "regit/base/%s" % s
        else:
            tmp = s.base

        if not _continue:
            if s.base:
                print("regit: updating base branch \"%s\"..." % s.base)
                s.base.update()
            for dep in deps:
                print("regit: updating dependency \"%s\"..." % dep)
                dep.update()

        print("regit: updating branch \"%s\"..." % s)

        to_merge = []
        if not _continue:
            if deps:
#                if not Branch.map.get(tmp):
                    tmp = Branch(tmp)
                    print("regit: checking out base branch \"%s\" into "
                            "temporary branch \"%s\"..." % (s.base, tmp))
                    Branch.switch(tmp, s.base)
#                else:
#                    tmp = Branch.map.get(tmp)
#                    print("regit: updating temporary branch \"%s\"..." % (tmp))
#                    Branch.switch(tmp)
#                    deps = [s.base] + deps

            else:
                tmp = s.base

            for dep in deps:
                if s.base == tmp or s.base == dep or not s.base.missing_from(dep):
                    if not s.base == dep:
                        print("regit: skipping dependency \"%s\" as it is already part of base \"%s\"" % (dep, s.base))
                    continue

                to_merge.append(dep)
        else:
            _tmp = _continue.get("already_done") or []
            for dep_name in _tmp:
                already_done.append(Branch(dep_name))

            for dep in deps:
                if not dep in already_done:
                    to_merge.append(dep)

            if deps:
                if str(tmp) in Branch.map:
                    Branch.switch(tmp)

        if to_merge:
            print("regit: merging dependenies %s into %s..." % (", ".join(str_list(to_merge)), s))
            for dep in to_merge:
                if not dep.based_on(s.base):
                    print("regit: warning: %s is not based on %s!" % (dep, s.base))

                try:
                    print("regit: merging branch %s..." % dep)
                    git_command(['merge', '--no-ff', '-m', 'DEPENDENCY MERGE: %s' % (dep), dep.name])
                    already_done.append(dep)
                except subprocess.CalledProcessError as e:
                    if is_automerge_complete():
                        print("regit: rerere merging seems to have succeeded. Continuing.")
                        git_command(['commit', '--no-edit'])
                        already_done.append(dep)
                    else:
                        state = {
                                'action' : 'update',
                                'branch' : str(s),
                                'base' : str(s.base),
                                'deps' : str_list(deps),
                                'conflict' : str(dep),
                                'already_done' : str_list(already_done),
                                }

                        statefile = open(state_file(), "w")
                        json.dump(state, statefile)

                        _err("regit: merging failed. manually fix conflicts, then run \"regit update\" to continue.")

        if deps or (tmp==s.base) or _continue:
            print("regit: rebasing \"%s\" onto \"%s\"..." % (s, tmp))
            Branch.switch(s)
            try:
                new_rebase_tip = tmp.head()
                git_command(['rebase', '--onto', str(tmp), s.rebase_tip])
                s.rebase_tip = new_rebase_tip
                s.update_branch_file()
            except subprocess.CalledProcessError as e:
                state = {
                        'action' : 'update',
                        'branch' : str(s),
                        'base' : str(s.base),
                        'deps' : str_list(deps),
                        }
                statefile = open(state_file(), "w")
                json.dump(state, statefile)
                _err("regit: rebasing failed. manually complete rebase, then run \"regit update\" to continue,"
                              "or \"regit abort\" to cancel.")

        elif Branch.current != s:
            Branch.switch(s)

        s.updated = True
        #if tmp != base:
        #    git_command(['branch', '-D', tmp])

    def needs_update(s, quiet=True):
        s.get_data()

        deps = s.deps
        if s.base:
            deps = [s.base] + deps

        deps = deps or []

        res = False
        for dep in deps:
            if dep.needs_update(quiet):
                if not quiet:
                    print("regit: branch \"%s\": dependency \"%s\" needs update." % (s, dep))
                    res = True
                else:
                    return True

            if s.missing_from(dep):
                if not quiet:
                    print("regit: branch \"%s\" needs to update \"%s\"." % (s, dep))
                    res = True
                else:
                    return True

        return res

    def missing_from(s, other):
        return branch_missing_commits(s.name, other.name)

    def get_deps(s, recursive=False, own=True):
        if not recursive:
            if own:
                return s.deps
            else:
                return []
        else:
            deps = set()
            if own:
                deps.update(s.deps or [])
            for dep in s.deps or []:
                deps.update(dep.get_deps(True))

        return sorted(list(deps), key=lambda x: x.name)

    def deps_depend_on(s, dep):
        for _dep in (s.deps or []):
            if _dep.depends_on(dep):
                return True
        return False

    def depends_on(s, dep):
        s.get_data()
        if s.base==dep:
            return True
        for _dep in (s.deps or []):
            if _dep == dep or _dep.depends_on(dep):
                return True
        if s.base:
            if s.base.depends_on(dep):
                return True
        return False

    def collect_dot_deps(s, outset):
        s.get_data()
        if s.base:
            outset.update(s.base.collect_dot_deps(outset))
            if not s.deps_depend_on(s.base):
                outset.add("\"%s\" -> \"%s\"" % (s, s.base))
        for dep in s.deps or []:
            outset.add("\"%s\" -> \"%s\"" % (s, dep))
            outset.update(dep.collect_dot_deps(outset))

        return outset

    def tmp_name(s):
        return "regit/tmp/%s" % s.name

    def export(s, name=None):
        merge_base = "regit/base/%s" % s.name
        if not name:
            name = "regit/export/%s" % s.name

        print("regit: exporting %s to %s..." % (s.name, name))

        s.get_data()
        branch = Branch.maybe_new(name)
        Branch.switch(branch, s.base)
        print(branch, s.base, merge_base)
        if s.deps:
            cmd_check("( cd \"%s\" ; git diff %s..%s | git apply --index )" % (
                topdir, s.base.name, merge_base))

            bfile = s.branch_file()

            commit_message = ("DEPENDENCY COMMIT\n\n"
                             "This commit contains the following dependencies:\n\n"
                             + s.dep_string("    ", True))
            if s.base:
                commit_message = commit_message + "\nBase branch: %s" % s.base

            git_command(['commit', '-m', commit_message])

        cmd_check("git format-patch %s..%s --stdout | git am --ignore-whitespace" % (
            merge_base, s.name))

    def dep_string(s, indent="    ", recursive=False):
        out = ""
        for dep in s.deps or []:
            out = out + "%s%s\n" % (indent+"  ", dep)
            if recursive:
                out = out + dep.dep_string(indent + "  ", True)

        return out

    def squash_deps(s):
        if Branch.current.name!=s.name:
            print("regit: error: squash_deps called from wrong active branch!")
            print(Branch.current, s)
            return False

        s.export(s.tmp_name())
        Branch.switch(s)
        git_command(['reset', '--hard', "refs/heads/%s" % s.tmp_name()])
        git_command(['branch', '-D', s.tmp_name()])

    def update_branch_file(s, bdict=None):
        if not bdict:
            bdict = {
                    'base' : s.base.name,
                    'deps' : str_list(s.deps or []),
                    "rebase_tip" : s.rebase_tip }

        json.dump(bdict, open(s.branch_file(), "w"))

    def branch_file(s):
        return os.path.join(topdir, ".git/regit/branches/%s" % s)

    def delete(s):
        if Branch.current==s:
            _err("regit: error: tried to delete current branch.")

        git_command(['branch', '-D', s.name])

    def delete_deps(s, deps):
        s.get_data()
        change=False
        deps = listify(deps)
        for dep in (deps or []):
            if dep in (s.deps or []):
                print("regit: removing dependency %s from branch %s." % (dep, s))
                s.deps.remove(dep)
                change=True
            if dep == s.base:
                if dep.base:
                    print("regit: setting base branch of %s (%s) as new base branch of %s" % (
                        dep, dep.base, s))
                    s.base = dep.base
                else:
                    s.base = None
                    print("regit: warning: deleting base branch %s of branch %s" %
                            (dep, s))
                change = True

        if change:
            s.update_branch_file()
            s.have_data = False

    def check_unmanaged_deps(s, base=None):
        s.get_data()
        if not (s.deps or s.base or base):
            print("regit: branch %s doesn't have dependency information." % s)
            return False

        res = True

        if not base:
            base = s.base
        else:
            if not s.base:
                res = s.based_on(base)
                if not s.based_on(base):
                    print("regit: branch %s needs rebase to %s!" % (s, base))
                    return False
                else:
                    print("regit: branch %s is based on %s." % (s, base))
                    return True

        for dep in s.deps or []:
             res = res and dep.check_unmanaged_deps(base)

        return res

#    def can_merge(s, other):
#        old_branch = Branch.current
#        old_head = s.head()
#        Branch.switch(s)

def str_list(list):
    res = []
    for x in list or []:
        res.append(str(x))
    return res

def git_command_output(cmd):
    git = [ 'git', ]
    git.extend(cmd)
    return subprocess.check_output(git, universal_newlines=True)

def git_command(cmd, quiet=False):
    git = [ 'git', ]
    git.extend(cmd)
    if quiet:
        out=subprocess.DEVNULL
        err=subprocess.DEVNULL
    else:
        out=sys.stdout
        err=sys.stderr

    return subprocess.check_call(git, stdout=out, stderr=err)

def _err(string):
    print(string,file=sys.stderr)
    sys.exit(1)


def state_file():
    return os.path.join(topdir, ".git/regit/state")

def load_state():
    try:
        statefile = open(state_file(), "r")
    except:
        return

    return json.load(statefile)

def check_state():
    state = load_state()
    if state:
        action = state.get('action')
        if action == "update":
            _err("regit: update in progress. fix conflicts and run \"git dep continue\"")
        else:
            _err("regit: statefile corrupt.")


def cmd_check(cmd):
    try:
        subprocess.check_output(cmd, shell=True)
        return True
    except subprocess.CalledProcessError:
        return False

# return true if a is missing commits from b
def branch_missing_commits(a, b):
    return cmd_check("test -n \"$(git log --invert-grep --grep=\"^DEPENDENCY UPDATE\" %s..%s)\"" % (a, b))

def git_workdir_clean():
    return cmd_check("test -z \"$(git status --porcelain -s | grep -v '^??')\"")

def is_automerge_complete():
    return cmd_check("test -z \"$(git status --porcelain -s | grep -vE '(^[ADMR]  )')\"")

def print_dependency_status(branch, deps):
    if deps:
        for dep in deps:
            if branch.missing_from(dep):
                print("  ->", end="")
            else:
                print("    ", end="")
            _tmp = ""
            if dep.needs_update():
                _tmp = " (needs update itself)"
            if dep.base and not dep.base.missing_from(dep):
                print("(%s)" % dep, _tmp)
            else:
                print("%s" % dep, _tmp)

def status(args):
    if not git_workdir_clean():
        _err("regit: workdir unclean. Please, commit your changes or stash them.")

    Branch.get(True)
    start_branch = Branch.current

    to_check = None
    if args.all:
        to_check = Branch.list
    else:
        to_check = [Branch.current]

    if args.show:
        args.dot = True

    dot_set = set()
    for branch in to_check:
        if not branch.has_branchfile():
            continue
        name = branch.name
        if name.startswith("regit/"):
            continue
        Branch.switch(branch)
        if not args.dot:
            if not branch.needs_update():
                print("regit: branch", branch, "is up to date.")
                if branch.base and not branch.base.missing_from(branch):
                    print("regit: branch %s is part of %s!" %(branch, branch.base))
            else:
                print("regit: branch", branch, "needs update.")
            _deps = branch.get_deps()
            if _deps:
                print("  dependencies:")
                print_dependency_status(branch, _deps)

            _deps = branch.get_deps(args.recursive_deps, False)
            if _deps:
                print("  indirect dependencies:")
                print_dependency_status(branch, _deps)
        else:
            branch.collect_dot_deps(dot_set)
    if args.dot:
        outfile = sys.stdout
        if args.show:
            fd, outfile_name = tempfile.mkstemp(suffix='.dot')
            outfile = os.fdopen(fd, 'w')

        print("digraph \"regit branch dependencies\" {", file=outfile)
        for entry in sorted(dot_set):
            print(entry, file=outfile)
        print("}", file=outfile)
        if args.show:
            outfile.close()
            pdf = outfile_name + ".pdf"
            cmd_check("cat %s | dot -Tpdf > %s" % (outfile_name, pdf))
            cmd_check("xdg-open %s" % pdf)
            os.unlink(outfile_name)
            os.unlink(pdf)

    if Branch.current!=start_branch:
        Branch.switch(start_branch)

def listify(something):
    if not something:
        return []
    if not type(something)==list:
        return [something]
    return something

def name_to_branch(list):
    list = listify(list)
    res = []
    for x in list:
        _tmp = Branch.map.get(x)
        if _tmp:
            res.append(_tmp)
    return res

def update(args):
    global current
    global branches

    state = load_state()
    if state:
        os.unlink(state_file())
        action = state.get('action')
        if action == "update":
            print("regit: continuing update.")
            Branch.get(True)
            branch = name_to_branch(state.get('branch'))[0]
            branch.update(state)
        else:
            _err("regit: error: update: tree is in a running \"%s\" operation." % action)

    else:
        if not git_workdir_clean():
            _err("regit: workdir unclean. Please, commit your changes or stash them.")
        Branch.get()
        to_update = Branch.current
        if to_update.check_unmanaged_deps(Branch.current.base):
            to_update.update()

def export(args):
    Branch.get()
    Branch.current.export(args.name)

def init(args):
    Branch.get()
    if os.path.isfile(Branch.current.branch_file()):
        _err("regit: error: branch dependency file (%s) already exists." % Branch.current.branch_file())

    base = Branch.map.get(args.base)
    if not base:
        _err("regit: base branch \"%s\" unknown. exiting." % args.base)

    rebase_tip = Branch.current.merge_base(base)

    if rebase_tip != base.head():
        _err("regit: branch %s needs rebase to %s!" % (Branch.current, base))

    bdict = { 'base' : args.base, 'deps' : args.depends_on, "rebase_tip" : rebase_tip }
    Branch.current.update_branch_file(bdict)

def add(args):
    Branch.get()
    f = open(Branch.current.branch_file(), 'r')
    bdict = json.load(f)
    deps = bdict.get('deps') or []
    for d in args.dep:
        if not d in deps:
            deps.append(d)
    bdict['deps'] = deps

    Branch.current.update_branch_file(bdict)

def ddel(args):
    Branch.get()

def show(args):
    Branch.get()

    b = Branch.current
    if not b.has_branchfile():
        print("regit: branch %s is not managed by regit." % b)
        return

    b.get_data()

    print("Branch......:", b)
    print("Base........:", b.base)
    print("Dependencies:", ", ".join(str_list(b.deps or [])))
    print("Rebase tip..:", b.rebase_tip)

def delete_branch(args):
    Branch.get()

    b = Branch.current

    for branch_name in args.branch:
        branch = Branch.map.get(branch_name)
        if not branch:
            print("regit: cannot delete nonexistent branch", branch_name)
            continue
        if branch==Branch.current:
            print("regit: cannot remove currently active branch.")
            continue

        print("regit: deleting branch %s" % branch)

        branch.get_data()

        for other in Branch.list:
            if other==branch:
                continue
            if not other.has_branchfile():
                continue

            other.delete_deps(branch)
        os.unlink(branch.branch_file())
        branch.delete()

    Branch.switch(b)

if __name__=="__main__":
    try:
        topdir = git_command_output(['rev-parse', '--show-toplevel']).rstrip()
    except subprocess.CalledProcessError:
        _err("regit: git error (cannot find repository root). Exiting.")

    parser = argparse.ArgumentParser(prog="regit")
    parser.set_defaults(func=None)
    subparsers = parser.add_subparsers(dest='cmd')
    parser_update = subparsers.add_parser("update", help="update branch(es)")
    parser_update.add_argument("--all", "-a",
            help="update all branches (default: only current + dependencies)",
            action='store_true'
            )
    parser_update.set_defaults(func=update)

    parser_init = subparsers.add_parser("init", help="initialize branch dependencies")
    parser_init.add_argument("--base", "-b", help="base branch (default: master)", default="master")
    parser_init.add_argument("--depends-on", "-d", help="branch dependencies (default: none)", nargs='*')
    parser_init.set_defaults(func=init)

    parser_add = subparsers.add_parser("add", help="add branch dependencies")
    parser_add.add_argument("dep", nargs='+')
    parser_add.set_defaults(func=add)

    parser_del = subparsers.add_parser("del", help="del branch dependencies")
    parser_del.add_argument("dep", nargs='+')
    parser_del.set_defaults(func=ddel)

    parser_show = subparsers.add_parser("show", help="show branch information")
    parser_show.set_defaults(func=show)

    parser_status = subparsers.add_parser("status", help="show branch dependency status")
    parser_status.add_argument("--all", "-a",
            help="show status of all branches (default: only current)",
            action='store_true'
            )
    parser_status.add_argument("--recursive-deps", "-r",
            help="show dependencies of dependencies (default: only show direct deps)",
            action='store_true'
            )
    parser_status.add_argument("--dot", "-d",
            help="output dependencies in graphviz' dot format  (default: print human readable)",
            action='store_true'
            )
    parser_status.add_argument("--show", "-s",
            help="show graph in pdf viewer",
            action='store_true'
            )
    parser_status.add_argument("--verbose", "-v",
            help="also print dependency and base branch status",
            action='store_true'
            )
    parser_status.set_defaults(func=status)

    parser_export = subparsers.add_parser("export", help="cleanly export a branch")
    parser_export.add_argument("--name", "-n", help="name of new branch. (default: regit/export/<branch>)", default=None)
    parser_export.set_defaults(func=export)

    parser_delete_branch = subparsers.add_parser("delete-branch", help="delete a branch, updating dependencies")
    parser_delete_branch.add_argument("branch", nargs='+', help="name of new branch to delete.", default=None)
    parser_delete_branch.set_defaults(func=delete_branch)

#    parser_add = subparsers.add_parser("filter", help="regit commit filter")
#    parser_add.add_argument("filter", choices = [')
#    parser_add.set_defaults(func=add)

    args = parser.parse_args()

    if not args.func:
        parser.print_help()
    else:
        args.func(args)
