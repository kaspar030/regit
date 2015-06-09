#!/bin/sh

# this script can be used as git merge driver.
# it will always use the local version of a file.
#
# regit uses this in order to prevent overwriting of
# .regit, the branch dependency file.
#
# (for rebase, the "ours" and "theirs" is reversed,
# so we use "theirs")

[ "$GIT_REFLOG_ACTION" = "rebase" ] && cp "$3" $2

true
