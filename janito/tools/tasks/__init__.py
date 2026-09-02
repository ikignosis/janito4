"""Task tools: StartTask, StopTask, WaitForTask and ListTasks.

StartTask / StopTask / WaitForTask cover the parallel-task lifecycle
(issue #94); ListTasks (issue #101) reports a blocking-free snapshot of every
task -- running *and* finished -- so the model (and, via the shell's
end-of-turn notice and confirm-quit prompt, the user) always knows what is
still in flight.
"""
