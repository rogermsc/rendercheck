# Regenerate the README animation:
#
#     brew install vhs && vhs docs/demo.tape
#
# Kept in the repo so the GIF can be rebuilt when the output changes, rather
# than slowly drifting from what the tool actually prints.

Output docs/demo.gif

Set Shell bash
Set FontSize 13
Set Width 1500
# Ten cases now rather than eight. Too short and the recording ends mid-scroll
# on the last one, which is the case a reader is most likely to still be reading.
Set Height 1100
Set Padding 20
Set Theme "Builtin Dark"
Set TypingSpeed 45ms
Set PlaybackSpeed 1.0

# The files are generated on first run; pre-warming keeps the recording from
# opening with four seconds of ffmpeg.
Hide
Type "rendercheck demo > /dev/null 2>&1"
Enter
Sleep 30s
Type "clear"
Enter
Show

Sleep 800ms
Type "rendercheck demo"
Sleep 400ms
Enter
Sleep 18s
