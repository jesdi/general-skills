
The driver put back these files at their locked versions, in a commit before this call, because the implementer or a fixer had changed them: {restored}.
A fixer may not change a locked file. When a test in one of these files now fails only because this ticket changed an interface that the test uses, update that file so it works with the new interface: keep what each test asserts, and list the file in `locked`. Else leave the file as it is.
