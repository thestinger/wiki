* edit previews
* search results should give context, with highlighted matches
* friendlier error handling (almost all invalid input just bubbles up an
  exception to a 500 error at the moment)
* navigation needs to be overhauled and styled sanely
* there is a lot of duplication between the html snippets, which should be
  dealt with via templates (static ones or just bottle's SimpleTemplate?)
* thread-safety (libgit2 synchronization requirements need to be dealt with)
* using sqlite's WAL mode might be a good idea
* the docutils html writer output could be nicer, as it uses some deprecated
  features and it should be updated to make use of modern semantic html
  elements
* one possibility for handling discussions is to use git-notes, like github
  does for commenting on commits
* diff between any revisions
* renaming articles
