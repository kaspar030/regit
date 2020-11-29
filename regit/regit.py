#!/usr/bin/env python

import argparse
import json
import os
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

    def __init__(self, name):
        self.name = name
        self.base = None
        self.deps = None
        self.rebase_tip = None
        self.updated = False
        self.have_data = False
        self.pr = None
        Branch.map[name] = self

    def switch(branch, base=None):
        if branch == Branch.current:
            return

        if base:
            git_command(["checkout", "-B", branch.name, base.name], True)
        else:
            git_command(["checkout", branch.name, "--"], True)

        Branch.current = branch

    def datafile():
        return os.path.join(topdir, ".regit")

    def maybe_new(name):
        existing = Branch.map.get(name)
        if existing:
            return existing

        return Branch(name)

    def __str__(self):
        return self.name

    def name_and_pr(self):
        if self.pr:
            return "%s (%s)" % (self.name, self.pr)
        else:
            return self.name

    def head(self):
        return git_command_output(["show-ref", "-s", "refs/heads/%s" % self]).rstrip()

    def merge_base(self, other):
        return git_command_output(["merge-base", other.name, self.name]).rstrip()

    def based_on(self, other):
        return self.merge_base(other) == other.head()

    def dependency_commit(self):
        x = "DEPENDENCY UPDATE"
        out = git_command_output(["log", "--pretty=oneline", "--grep=^%s$" % x])
        out = out.splitlines()
        if not out:
            return None
        if len(out) > 1:
            print("regit: warning: multiple dependency commits.\n")
            return None
        m = re_commit_oneline.match(out[0])
        if not m:
            return None

        return m.group("hash")

    def get(have_state=False):
        branches = {}
        out = git_command_output(["branch"])
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

    def has_branchfile(self):
        return os.path.isfile(self.branch_file())

    def get_data(self):
        if self.have_data:
            return

        if not self.has_branchfile():
            return False

        Branch.switch(self)

        f = open(self.branch_file(), "r")
        bdict = json.load(f)
        base = bdict.get("base")
        if not base:
            _err("regit: branch dependency file has no base branch! exiting.")
        self.base = Branch.map.get(base)
        if not self.base:
            _err(
                'regit: branch "%s" has unknown base branch "%s". exiting.'
                % (self.name, base)
            )

        rebase_tip = bdict.get("rebase_tip")
        if not rebase_tip:
            _err('regit: branch "%s" has unset rebase tip. exiting.' % (self.name))
        self.rebase_tip = rebase_tip

        self.pr = bdict.get("pr")
        _deps = bdict.get("deps")
        deps = []
        if _deps:
            for branch in _deps:
                if branch == base:
                    print(
                        'regit: warning: branch "%s" depends on it\'s base branch.' % self
                    )
                elif branch == self.name:
                    print('regit: warning: branch "%s" depends on itself.' % self)
                elif branch in deps:
                    print(
                        'regit: warning: branch "%s" has duplicate '
                        'dependency "%s".' % (self, branch)
                    )
                else:
                    deps.append(branch)

        self.deps = []
        for dep in deps:
            b = Branch.map.get(dep)
            if not b:
                print(
                    'regit: error: branch "%s" depends on '
                    'unknown branch "%s".' % (self.name, dep)
                )
            else:
                self.deps.append(b)
        self.have_data = True

    def update(self, _continue=False, recursive=False):
        if self.updated:
            print("regit: skipping already updated branch")
            return

        self.get_data()
        deps = self.deps or []
        already_done = []

        if (not self.base) and (not self.deps):
            print('regit: no dependency information for branch "%s".' % self)
            self.updated = True
            return

        tmp = None
        if deps:
            if _continue:
                tmp = Branch.maybe_new("regit/base/%s" % self.name)
            else:
                tmp = "regit/base/%s" % self.name
        else:
            tmp = self.base

        if recursive and not _continue:
            if self.base:
                if self != self.base:
                    print('regit: updating base branch "%s"...' % self.base)
                    self.base.update(recursive=recursive)
            for dep in deps:
                print('regit: updating dependency "%s"...' % dep)
                dep.update(recursive=recursive)

        print('regit: updating branch "%s"...' % self)

        to_merge = []
        if not _continue:
            if deps:
                #                if not Branch.map.get(tmp):
                tmp = Branch(tmp)
                print(
                    'regit: checking out base branch "%s" into '
                    'intermediate branch "%s"...' % (self.base, tmp)
                )
                Branch.switch(tmp, self.base)
            #                else:
            #                    tmp = Branch.map.get(tmp)
            #                    print("regit: updating temporary branch \"%s\"..." % (tmp))
            #                    Branch.switch(tmp)
            #                    deps = [s.base] + deps

            else:
                tmp = self.base

            for dep in deps:
                if self.base == tmp or self.base == dep or not self.base.missing_from(dep):
                    if not self.base == dep:
                        print(
                            'regit: skipping dependency "%s" as it is already part of base "%s"'
                            % (dep, self.base)
                        )
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
            print(
                "regit: merging dependencies %s into %s..."
                % (", ".join(str_list(to_merge)), tmp)
            )
            for dep in to_merge:
                if not dep.based_on(self.base):
                    print("regit: warning: %s is not based on %s!" % (dep, self.base))

                try:
                    print("regit: merging branch %s..." % dep.name_and_pr())
                    git_command(
                        [
                            "merge",
                            "--no-ff",
                            "-m",
                            "DEPENDENCY MERGE: %s" % (dep.name_and_pr()),
                            dep.name,
                        ]
                    )
                    already_done.append(dep)
                except subprocess.CalledProcessError as e:
                    if is_automerge_complete():
                        print(
                            "regit: rerere merging seems to have succeeded. Continuing."
                        )
                        git_command(["commit", "--no-edit"])
                        already_done.append(dep)
                    else:
                        state = {
                            "action": "update",
                            "phase": "merge",
                            "branch": str(self),
                            "base": str(self.base),
                            "deps": str_list(deps),
                            "conflict": str(dep),
                            "already_done": str_list(already_done),
                        }

                        statefile = open(state_file(), "w")
                        json.dump(state, statefile)

                        _err(
                            'regit: merging failed (probably due to conflixts).' \
                            'regit: manually fix conflicts, finish the merge (git merge --continue),' \
                            'regit: then run "git dep --continue".'
                        )

        if deps or (tmp == self.base) or _continue:
            rebase_tmp = Branch.maybe_new("regit/tmp/%s" % self.name)

            print('regit: rebasing "%s" onto "%s"...' % (self, rebase_tmp))
            Branch.switch(self)
            Branch.switch(rebase_tmp, self)
            try:
                new_rebase_tip = tmp.head()
                git_command(["rebase", "--onto", str(tmp), self.rebase_tip])
                Branch.switch(self, rebase_tmp)
                rebase_tmp.delete()
                self.rebase_tip = new_rebase_tip
                self.update_branch_file()
            except subprocess.CalledProcessError as e:
                while True:
                    if is_automerge_complete():
                        print("regit: rerere auto-resolving of conflicts succeeded.")
                        try:
                            git_command(["rebase", "--continue"])
                            Branch.switch(self, rebase_tmp)
                            break
                        except subprocess.CalledProcessError:
                            pass
                    else:
                        self.save_rebase_state(deps, new_rebase_tip)

        elif Branch.current != self:
            Branch.switch(self)

        self.updated = True
        # if tmp != base:
        #    git_command(['branch', '-D', tmp])

    def finish_rebase(self, state):
        rebase_tmp = Branch.maybe_new("regit/tmp/%s" % self)
        Branch.switch(rebase_tmp)
        Branch.switch(self, rebase_tmp)
        rebase_tmp.delete()
        self.rebase_tip = state.get("new_rebase_tip")
        self.update_branch_file()

    def abort_rebase(self, state):
        print("regit: aborting.")
        branch = Branch.maybe_new(state.get("branch"))

        if get_rebase_head_name() == "refs/heads/regit/tmp/%s" % branch:
            git_command(["rebase", "--abort"])

        Branch.switch(branch)

    def save_rebase_state(self, deps, new_rebase_tip):
        state = {
            "action": "update",
            "phase": "rebase",
            "branch": str(self),
            "base": str(self.base),
            "deps": str_list(deps),
            "new_rebase_tip": new_rebase_tip,
        }
        statefile = open(state_file(), "w")
        json.dump(state, statefile)
        _err(
            "regit: rebasing failed. manually complete rebase, then\n"
            'regit: run "git dep --continue" if the rebase succeeded,\n'
            'regit: or "git dep --abort" to cancel.'
        )

    def needs_update(self, quiet=True):
        self.get_data()

        deps = self.deps
        if self.base:
            deps = [self.base] + deps

        deps = deps or []

        res = False
        for dep in deps:
            if dep.needs_update(quiet):
                if not quiet:
                    print(
                        'regit: branch "%s": dependency "%s" needs update.' % (self, dep)
                    )
                    res = True
                else:
                    return True

            if self.missing_from(dep):
                if not quiet:
                    print('regit: branch "%s" needs to update "%s".' % (self, dep))
                    res = True
                else:
                    return True

        return res

    def missing_from(self, other):
        return branch_missing_commits(self.name, other.name)

    def get_deps(self, recursive=False, own=True):
        if not recursive:
            if own:
                return self.deps
            else:
                return []
        else:
            deps = set()
            if own:
                deps.update(self.deps or [])
            for dep in self.deps or []:
                deps.update(dep.get_deps(True))

        return sorted(list(deps), key=lambda x: x.name)

    def deps_depend_on(self, dep):
        for _dep in self.deps or []:
            if _dep.depends_on(dep):
                return True
        return False

    def depends_on(self, dep):
        self.get_data()
        if self.base == dep:
            return True
        for _dep in self.deps or []:
            if _dep == dep or _dep.depends_on(dep):
                return True
        if self.base:
            if self.base.depends_on(dep):
                return True
        return False

    def collect_dot_deps(self, outset):
        self.get_data()
        if self.base:
            outset.update(self.base.collect_dot_deps(outset))
            if not self.deps_depend_on(self.base):
                outset.add('"%s" -> "%s"' % (self.name_and_pr(), self.base))
        for dep in self.deps or []:
            dep.get_data()
            outset.add('"%s" -> "%s"' % (self.name_and_pr(), dep.name_and_pr()))
            outset.update(dep.collect_dot_deps(outset))

        return outset

    def tmp_name(self):
        return "regit/tmp/%s" % self.name

    def export(self, name=None):
        merge_base = "regit/base/%s" % self.name
        if not name:
            name = "regit/export/%s" % self.name

        print("regit: exporting %s to %s..." % (self.name, name))

        self.get_data()
        branch = Branch.maybe_new(name)
        Branch.switch(branch, self.base)

        if self.deps:
            cmd_check(
                '( cd "%s" ; git diff %s..%s | git apply --index )'
                % (topdir, self.base.name, merge_base)
            )

            bfile = self.branch_file()

            commit_message = (
                "DEPENDENCY COMMIT\n\n"
                "This commit contains the following dependencies:\n\n"
                + self.dep_string("    ", True)
            )
            if self.base:
                commit_message = commit_message + "\nBase branch: %s" % self.base

            git_command(["commit", "-m", commit_message])

        cmd_check(
            "git format-patch %s..%s --stdout | git am --ignore-whitespace"
            % (merge_base, self.name)
        )

    def dep_string(self, indent="    ", recursive=False):
        out = ""
        for dep in self.deps or []:
            out = out + "%s%s\n" % (indent + "  ", dep)
            if recursive:
                out = out + dep.dep_string(indent + "  ", True)

        return out

    def squash_deps(self):
        if Branch.current.name != self.name:
            print("regit: error: squash_deps called from wrong active branch!")
            print(Branch.current, self)
            return False

        self.export(self.tmp_name())
        Branch.switch(self)
        git_command(["reset", "--hard", "refs/heads/%s" % self.tmp_name()])
        git_command(["branch", "-D", self.tmp_name()])

    def update_branch_file(self, bdict=None):
        if not bdict:
            bdict = {
                "base": self.base.name,
                "deps": str_list(self.deps or []),
                "rebase_tip": self.rebase_tip,
            }
            if self.pr is not None:
                bdict["pr"] = self.pr

        json.dump(bdict, open(self.branch_file(), "w"))

    def branch_file(self):
        branch_name = self.name.replace("/", "__")

        return os.path.join(topdir, ".git/regit/branches/%s" % branch_name)

    def delete(self):
        if Branch.current == self:
            _err("regit: error: tried to delete current branch.")

        git_command(["branch", "-D", self.name])

    def delete_deps(self, deps):
        self.get_data()
        change = False
        deps = listify(deps)
        for dep in deps or []:
            if dep in (self.deps or []):
                print("regit: removing dependency %s from branch %s." % (dep, self))
                self.deps.remove(dep)
                change = True
            if dep == self.base:
                if dep.base:
                    print(
                        "regit: setting base branch of %s (%s) as new base branch of %s"
                        % (dep, dep.base, self)
                    )
                    self.base = dep.base
                else:
                    self.base = None
                    print(
                        "regit: warning: deleting base branch %s of branch %s"
                        % (dep, self)
                    )
                change = True

        if change:
            self.update_branch_file()
            self.have_data = False

    def check_unmanaged_deps(self, base=None):
        self.get_data()
        if not (self.deps or self.base or base):
            print("regit: branch %s doesn't have dependency information." % self)
            return False

        res = True

        if not base:
            base = self.base
        else:
            if not self.base:
                res = self.based_on(base)
                if not self.based_on(base):
                    print("regit: branch %s needs rebase to %s!" % (self, base))
                    return False
                else:
                    print("regit: branch %s is based on %s." % (self, base))
                    return True

        for dep in self.deps or []:
            res = res and dep.check_unmanaged_deps(base)

        return res


#    def can_merge(s, other):
#        old_branch = Branch.current
#        old_head = s.head()
#        Branch.switch(s)


def get_rebase_head_name():
    f = os.path.join(topdir, ".git/rebase-apply/head-name")
    if os.path.isfile(f):
        return open(f, "r").readline().rstrip()
    else:
        return None


def str_list(list):
    res = []
    for x in list or []:
        res.append(str(x))
    return res


def git_command_output(cmd):
    git = ["git"]
    git.extend(cmd)
    return subprocess.check_output(
        git, universal_newlines=True, stderr=subprocess.DEVNULL
    )


def git_command(cmd, quiet=False):
    git = ["git"]
    git.extend(cmd)
    if quiet:
        out = subprocess.DEVNULL
        err = subprocess.DEVNULL
    else:
        out = sys.stdout
        err = sys.stderr

    return subprocess.check_call(git, stdout=out, stderr=err)


def _err(string):
    print(string, file=sys.stderr)
    sys.exit(1)


def state_file():
    return os.path.join(topdir, ".git/regit/state")


def load_state():
    try:
        statefile = open(state_file(), "r")
    except:
        return None

    try:
        return json.load(statefile)
    except ValueError:
        return None


def cmd_check(cmd):
    try:
        subprocess.check_output(cmd, shell=True)
        return True
    except subprocess.CalledProcessError:
        return False


# return true if a is missing commits from b
def branch_missing_commits(a, b):
    return cmd_check(
        'test -n "$(git log --invert-grep --grep="^DEPENDENCY UPDATE" %s..%s)"' % (a, b)
    )


def git_workdir_clean():
    return cmd_check("test -z \"$(git status --porcelain -s | grep -v '^??')\"")


def is_automerge_complete():
    return cmd_check(
        "test -z \"$(git status --porcelain -s | grep -vE '(^[ADMR]  )' | grep -v '^??')\""
    )


def rev_parse(ref):
    try:
        ref = git_command_output(["rev-parse", ref])
        return ref.rstrip()
    except subprocess.CalledProcessError:
        return None


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
                print("(%s)" % dep.name_and_pr(), _tmp)
            else:
                print("%s" % dep.name_and_pr(), _tmp)


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
                print("regit: branch", branch.name_and_pr(), "is up to date.")
                if branch.base and not branch.base.missing_from(branch):
                    print("regit: branch %s is part of %s!" % (branch, branch.base))
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
            fd, outfile_name = tempfile.mkstemp(suffix=".dot")
            outfile = os.fdopen(fd, "w")

        print('digraph "regit branch dependencies" {', file=outfile)
        for entry in sorted(dot_set):
            print(entry, file=outfile)
        print("}", file=outfile)
        if args.show:
            outfile.close()
            pdf = outfile_name + ".pdf"
            cmd_check("cat %s | dot -Tpdf > %s" % (outfile_name, pdf))
            try:
                cmd_check("xdg-open %s" % pdf)
            except subprocess.CalledProcessError:
                pass
            os.unlink(outfile_name)
            os.unlink(pdf)

    if Branch.current != start_branch:
        Branch.switch(start_branch)


def listify(something):
    if not something:
        return []
    if not type(something) == list:
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


# Command line handler


def update(args):
    global current
    global branches

    if not git_workdir_clean():
        _err("regit: workdir unclean. Please, commit your changes or stash them.")

    Branch.get()
    to_update = Branch.current
    if to_update.check_unmanaged_deps(Branch.current.base):
        to_update.update(False, args.recursive)


def export(args):
    Branch.get()
    Branch.current.export(args.name)


def init(args):
    Branch.get()
    if Branch.current is None:
        _err("regit: cannot determine current branch")

    if os.path.isfile(Branch.current.branch_file()):
        _err(
            "regit: error: branch dependency file (%s) already exists."
            % Branch.current.branch_file()
        )

    base = Branch.map.get(args.base)
    if not base:
        _err('regit: base branch "%s" unknown. exiting.' % args.base)

    rebase_tip = Branch.current.merge_base(base)

    if rebase_tip != base.head():
        _err("regit: branch %s needs rebase to %s!" % (Branch.current, base))

    bdict = {"base": args.base, "deps": args.depends_on, "rebase_tip": rebase_tip}
    Branch.current.update_branch_file(bdict)


def add(args):
    Branch.get()
    f = open(Branch.current.branch_file(), "r")
    bdict = json.load(f)
    deps = bdict.get("deps") or []
    for d in args.dep:
        if not d in deps:
            deps.append(d)
    bdict["deps"] = deps

    Branch.current.update_branch_file(bdict)


def ddel(args):
    Branch.get()
    f = open(Branch.current.branch_file(), "r")
    bdict = json.load(f)
    _deps = bdict.get("deps") or []
    deps = []
    for d in _deps:
        if not d in args.dep:
            deps.append(d)
    bdict["deps"] = deps

    Branch.current.update_branch_file(bdict)


def dset(args):
    Branch.get()
    f = open(Branch.current.branch_file(), "r")
    bdict = json.load(f)
    bdict["deps"] = args.dep

    Branch.current.update_branch_file(bdict)


def show(args):
    Branch.get()

    b = Branch.current
    if not b.has_branchfile():
        print("regit: branch %s is not managed by regit." % b)
        return

    b.get_data()
    for dep in b.deps or []:
        dep.get_data()

    if Branch.current != b:
        Branch.switch(b)

    print("Branch......:", b.name_and_pr())
    print("Base........:", b.base)
    print("Dependencies:", ", ".join([x.name_and_pr() for x in b.deps or []]))
    print("Rebase tip..:", b.rebase_tip)


def delete_branch(args):
    Branch.get()

    b = Branch.current

    for branch_name in args.branch:
        branch = Branch.map.get(branch_name)
        if not branch:
            print("regit: cannot delete nonexistent branch", branch_name)
            continue
        if branch == Branch.current:
            print("regit: cannot remove currently active branch.")
            continue

        print("regit: deleting branch %s" % branch)

        branch.get_data()

        for other in Branch.list:
            if other == branch:
                continue
            if not other.has_branchfile():
                continue

            other.delete_deps(branch)
        try:
            os.unlink(branch.branch_file())
        except FileNotFoundError:
            pass

        branch.delete()

    Branch.switch(b)


def handle_state(args):
    state = load_state()
    if state:
        os.unlink(state_file())
        action = state.get("action")
        if action == "update":
            phase = state.get("phase")
            Branch.get(True)
            branch = name_to_branch(state.get("branch"))[0]

            if args.cont:
                branch.get_data()

                if phase == "merge":
                    print("regit: continuing update.")
                    branch.update(state)

                elif phase == "rebase":
                    print("regit: continuing rebase of branch %s." % branch)
                    branch.finish_rebase(state)
            else:
                if phase == "merge":
                    print("regit: aborting merge.")

                elif phase == "rebase":
                    branch.abort_rebase(state)
        else:
            _err('regit: error: update: tree is in a running "%s" operation.' % action)
    else:
        _err("regit: error while parsing statefile (%s)" % (state_file()))


def set_rebase_tip(args):
    if not (args.commit or args.base):
        _err(
            "regit: set-rebase-tip: either a commit reference or --base/-b are required!"
        )

    if args.commit and args.base:
        _err(
            "regit: set-rebase-tip: only commit reference *or* --base/-b can be specified!"
        )

    Branch.get()
    b = Branch.current
    b.get_data()

    rev = None

    if args.commit:
        rev = rev_parse(args.commit)
        if not rev:
            _err('regit: invalid commit reference "%s"' % args.commit)

    else:
        rev = b.base.head()

    b.rebase_tip = rev
    b.update_branch_file()

    print("regit: set rebase tip of branch %s to %s." % (b.name, b.rebase_tip[:8]))


def set_pr(args):
    if not (args.pr):
        _err(
            "regit: please specify PR number!"
        )

    pr = args.pr[0]
    if pr.startswith("#"):
        pr = pr[1:]

    try:
        pr = int(pr)
    except TypeError:
        _err("regit: please specify PR number as \"#12345\" or just \"12345\"!")

    Branch.get()
    b = Branch.current
    b.get_data()
    b.pr = "#%s" % pr
    b.update_branch_file()

    print("regit: set PR# of branch %s to %s." % (b, b.pr))


def main():
    os.environ["REGIT"] = "1"

    try:
        global topdir
        topdir = git_command_output(["rev-parse", "--show-toplevel"]).rstrip()
    except subprocess.CalledProcessError:
        _err("regit: git error (cannot find repository root). Exiting.")

    os.makedirs(os.path.join(topdir, ".git", "regit", "branches"), exist_ok=True)

    parser = argparse.ArgumentParser(prog="git dep")

    group = parser.add_argument_group(
        "handling running operations",
        "These options are only valid if a current operation is in progress, e.g., a manual conflict resolve.",
    )
    group.add_argument(
        "--continue",
        "-c",
        action="store_true",
        help="continue with currently running operation",
        dest="cont",
    )

    group.add_argument(
        "--abort", "-a", action="store_true", help="abort currently running operation"
    )

    parser.set_defaults(func=None)
    subparsers = parser.add_subparsers(dest="cmd")
    parser_update = subparsers.add_parser("update", help="update branch(es)")
    parser_update.add_argument(
        "--all",
        "-a",
        help="update all branches (default: only current)",
        action="store_true",
    )
    parser_update.add_argument(
        "--recursive",
        "-r",
        help="recurse to dependency branches (default: only current)",
        action="store_true",
    )
    parser_update.set_defaults(func=update)

    parser_init = subparsers.add_parser("init", help="initialize branch dependencies")
    parser_init.add_argument(
        "--base", "-b", help="base branch (default: master)", default="master"
    )
    parser_init.add_argument(
        "--depends-on", "-d", help="branch dependencies (default: none)", nargs="*"
    )
    parser_init.set_defaults(func=init)

    parser_add = subparsers.add_parser("add", help="add branch dependencies")
    parser_add.add_argument("dep", nargs="+")
    parser_add.set_defaults(func=add)

    parser_del = subparsers.add_parser("del", help="del branch dependencies")
    parser_del.add_argument("dep", nargs="+")
    parser_del.set_defaults(func=ddel)

    parser_set = subparsers.add_parser("set", help="set branch dependencies")
    parser_set.add_argument("dep", nargs="+")
    parser_set.set_defaults(func=dset)

    parser_show = subparsers.add_parser("show", help="show branch information")
    parser_show.set_defaults(func=show)

    parser_status = subparsers.add_parser(
        "status", help="show branch dependency status"
    )
    parser_status.add_argument(
        "--all",
        "-a",
        help="show status of all branches (default: only current)",
        action="store_true",
    )
    parser_status.add_argument(
        "--recursive-deps",
        "-r",
        help="show dependencies of dependencies (default: only show direct deps)",
        action="store_true",
    )
    parser_status.add_argument(
        "--dot",
        "-d",
        help="output dependencies in graphviz' dot format  (default: print human readable)",
        action="store_true",
    )
    parser_status.add_argument(
        "--show", "-s", help="show graph in pdf viewer", action="store_true"
    )
    parser_status.add_argument(
        "--verbose",
        "-v",
        help="also print dependency and base branch status",
        action="store_true",
    )
    parser_status.set_defaults(func=status)

    parser_export = subparsers.add_parser("export", help="cleanly export a branch")
    parser_export.add_argument(
        "--name",
        "-n",
        help="name of new branch. (default: regit/export/<branch>)",
        default=None,
    )
    parser_export.set_defaults(func=export)

    parser_delete_branch = subparsers.add_parser(
        "delete-branch", help="delete a branch, updating dependencies"
    )
    parser_delete_branch.add_argument(
        "branch", nargs="+", help="name of new branch to delete.", default=None
    )
    parser_delete_branch.set_defaults(func=delete_branch)

    parser_set_rebase_tip = subparsers.add_parser(
        "set-rebase-tip", help="update the branches rebase tip"
    )
    parser_set_rebase_tip.add_argument(
        "commit",
        nargs="?",
        help="git commit reference to use as new rebase tip",
        default=None,
    )
    parser_set_rebase_tip.add_argument(
        "--base", "-b", help="use head of base branch", action="store_true"
    )
    parser_set_rebase_tip.set_defaults(func=set_rebase_tip)

    #
    parser_set_pr = subparsers.add_parser(
        "set-pr", help="update the branches rebase tip"
    )
    parser_set_pr.add_argument(
        "pr",
        nargs=1,
        help="pull request: [#]<12345>",
    )
    parser_set_pr.set_defaults(func=set_pr)

    #    parser_add = subparsers.add_parser("filter", help="regit commit filter")
    #    parser_add.add_argument("filter", choices = [')
    #    parser_add.set_defaults(func=add)

    args = parser.parse_args()

    if os.path.isfile(state_file()):
        if not (args.cont or args.abort):
            _err("regit: operation in progress but no state command given.")
        else:
            handle_state(args)
            return

    if not args.func:
        parser.print_help()
    else:
        args.func(args)
