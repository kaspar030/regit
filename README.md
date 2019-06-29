# regit: keep those branches rebased

regit is a git add-on for managing branch dependencies.

## Why

Many github projects are using a pull request based workflow. Developers
who believe their feature branch is ready for merging open a pull request,
and after some time, someone might or might not press the merged button, and
the branch gets merged.

If you now have your branch (let's call it A) PR'ed, and you'd like to do some
work based on that branch in another branch (let's call that B), and also PR
that, the new PR will contain all the commits from branch A, possibly
cluttering the commit list.  Now if a reviewer finds a flaw in A that needs
fixing, in order to reflect those changes in B, that branch needs to be rebased
on A.  Not a big deal with just two branches. But if you're trying to keep your
PR's small and to the point, you might end up with a lot of interdependend
branches, like "driver D needs generic_driver C, which itself depends on
core_fix A and B". Now any change to "core_fix A" implies a cascade of manually
updating C and D.

regit helps with that.

## How to use
