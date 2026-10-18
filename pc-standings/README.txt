DraftKings standings exports go here.

1. On DraftKings, open a contest you entered once it has locked and download
   its standings (a .zip file).
2. Drop the .zip in this folder as it is (no need to unzip it).
3. That's all. The Vigil PC worker reads each new file once, within a few
   minutes, and the summary shows on the DFS tab under "Real fields".

Nothing in this folder is ever uploaded: git ignores every file here except
this README. The exports are large and list every entrant's username, so
they stay on this PC. The worker keeps only a small summary (ownership,
score lines, how the field built its lineups), with no usernames.
