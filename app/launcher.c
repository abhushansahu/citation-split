// Tiny launcher: runs the split as a child so macOS attributes its microphone
// use to this app bundle (which declares NSMicrophoneUsageDescription).
#include <spawn.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <mach-o/dyld.h>
#include <libgen.h>
extern char **environ;
static pid_t child = 0;
static void fwd(int sig) { if (child > 0) kill(child, sig); }
int main(int argc, char **argv) {
    char exe[4096]; uint32_t n = sizeof exe; _NSGetExecutablePath(exe, &n);
    char root[4096]; strcpy(root, dirname(exe)); // Contents/MacOS
    strcat(root, "/../../../.."); // MacOS -> Contents -> .app -> app/ -> project dir
    char py[4200], script[4200]; snprintf(py, sizeof py, "%s/venv/bin/python", root); snprintf(script, sizeof script, "%s/split.py", root);
    char *args[argc + 3]; args[0] = py; args[1] = script;
    for (int i = 1; i < argc; i++) args[i + 1] = argv[i];
    args[argc + 1] = NULL;
    signal(SIGTERM, fwd); signal(SIGINT, fwd); signal(SIGUSR1, fwd); signal(SIGUSR2, fwd);
    int rc = posix_spawn(&child, py, NULL, NULL, args, environ);
    if (rc != 0) { fprintf(stderr, "spawn %s: %s\n", py, strerror(rc)); return 1; }
    int st = 0; waitpid(child, &st, 0);
    return WIFEXITED(st) ? WEXITSTATUS(st) : 1;
}
