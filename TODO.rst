* diffs (plain text, and visual html diffs)
* edit previews
* search results should give context, with highlighted matches
* friendlier error handling (almost all invalid input just bubbles up an
  exception to a 500 error at the moment)
* editing interfaces need to verify that the content is valid before allowing
  it to be committed
* navigation needs to be overhauled and styled sanely
* there is a lot of duplication between the html snippets, which should be
  dealt with via templates (static ones or just bottle's SimpleTemplate?)
* thread-safety (sqlite needs to be put in WAL mode, and any libgit2
  synchronization requirements need to be dealt with)
* the docutils html writer output could be nicer - it doesn't actually output
  valid xhtml, uses some deprecated features and it should be updated to make
  use of modern semantic html elements
