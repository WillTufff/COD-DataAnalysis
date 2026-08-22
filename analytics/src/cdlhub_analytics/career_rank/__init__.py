"""All-time player ranking engine."""

# The first season this model publishes, and the first year a chip count is
# read as a career total. Its own constant, deliberately not
# `maprows.PUBLISHED_FROM_YEAR`: that one holds the floor for the site and for
# the evaluation harness, where a forward test run on seasons nobody can see
# measures nothing, and moving it would move both. An all-time board asks a
# different question. A board that admits no season before 2017 does not rank
# the 2013-2016 era low, it leaves the era out, and the `era_balance` face
# validity test fails on an unrepresented era for exactly that reason.
#
# What makes the earlier seasons comparable is not the year but the shrinkage
# in `breadth.shrink`: a 2013-2016 season is a mean of about half as many
# percentiles over about a third as many maps, so untouched it regresses to
# the middle less and posts fatter tails than a league season on the same
# player. The floor and the shrinkage land together or the era gate clears on
# that artifact instead of on the record.
#
# It lives here rather than in `engine`, because `anchors` reads it too and
# the orchestrator imports `anchors`.
PUBLISH_FROM_YEAR = 2013
