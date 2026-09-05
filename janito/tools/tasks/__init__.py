"""Task tools: StartTask, StopTask, WaitForTask, ListTasks and GetTaskInfo.

StartTask / StopTask / WaitForTask cover the parallel-task lifecycle
(issue #94); ListTasks (issue #101) reports a blocking-free snapshot of every
task -- running *and* finished -- so the model (and, via the shell's
end-of-turn notice and confirm-quit prompt, the user) always knows what is
still in flight. GetTaskInfo (issue #117) returns the full detail of a single
task, including its description and stdout/stderr filenames.
"""
