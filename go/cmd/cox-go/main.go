// cox-go is the phase 1 spike of the Go CLI; the Python `cox` stays the shipped CLI.
package main

import (
	"cmp"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	coxgo "github.com/ppfenning/coxswain-tools/go"
)

const defaultColumns = 80

// columnsFrom reads a COLUMNS value the way shutil.get_terminal_size does: positive integer or the default.
func columnsFrom(env string) int {
	if n, err := strconv.Atoi(env); err == nil && n > 0 {
		return n
	}
	return defaultColumns
}

func isHelp(arg string) bool { return arg == "-h" || arg == "--help" }

// route turns a help argv into what goes to stdout, what goes to stderr, and the exit code.
// handled is false for every other argv, which main hands to the Python cox.
func route(table coxgo.Table, args []string, columns int) (stdout, stderr string, code int, handled bool) {
	width := columns - 2
	var help string
	var err error
	switch {
	case len(args) == 2 && isHelp(args[1]):
		help, err = coxgo.GroupHelp(table, args[0], width)
	case len(args) == 3 && isHelp(args[2]):
		help, err = coxgo.CommandHelp(table, args[0], args[1], width)
	case len(args) == 4 && isHelp(args[3]):
		help, err = coxgo.SubcommandHelp(table, args[0], args[1], args[2], width)
	default:
		return "", "", 0, false
	}
	if err != nil {
		return "", fmt.Sprintf("cox-go: %v\n", err), 2, true
	}
	return help, "", 0, true
}

// isUsageAssess is true for `usage assess` with anything but a lone help flag, which still routes to help.
func isUsageAssess(args []string) bool {
	return len(args) >= 2 && args[0] == "usage" && args[1] == "assess" && !(len(args) == 3 && isHelp(args[2]))
}

// usageAssess prints `<verdict>: <reason>` and exits with the verdict's code. The clock and
// environment come in as arguments; COX_NOW, an RFC 3339 UTC timestamp, overrides the clock,
// and COX_NO_CCUSAGE=1 skips the ccusage call. handled is false for --json, a flag Go rejects,
// or a stray positional, all of which the Python cox answers.
func usageAssess(args []string, getenv func(string) string, clock func() time.Time) (stdout, stderr string, code int, handled bool) {
	fs := flag.NewFlagSet("usage assess", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	asJSON := fs.Bool("json", false, "")
	runsDir := fs.String("runs-dir", "runs", "")
	profile := fs.String("profile", "", "")
	if err := fs.Parse(args); err != nil || *asJSON || fs.NArg() > 0 {
		return "", "", 0, false
	}
	now := clock().UTC()
	if v := getenv("COX_NOW"); v != "" {
		parsed, err := time.Parse(time.RFC3339, v)
		if err != nil {
			return "", fmt.Sprintf("cox-go: COX_NOW %q: %v\n", v, err), 2, true
		}
		now = parsed
	}
	path := cmp.Or(*profile, getenv("AGENT_TOOLS_PROFILE"), "~/.config/agent-tools/profile.yaml")
	if rest, ok := strings.CutPrefix(path, "~/"); ok {
		path = filepath.Join(getenv("HOME"), rest)
	}
	r := coxgo.Assess(*runsDir, path, now, coxgo.CachedBlocks(*runsDir, now, getenv, func() []byte { return coxgo.CcusageBlocks(getenv) }))
	return r.Line() + "\n", "", r.Code, true
}

// wantsHelp is true for the group, command and subcommand help argvs that route answers.
func wantsHelp(args []string) bool {
	return len(args) >= 2 && len(args) <= 4 && isHelp(args[len(args)-1])
}

// native answers what cox-go has ported; handled is false for every argv the Python cox must take.
// The table is loaded only for help, and a table that fails to load hands help to Python too.
func native(args []string, getenv func(string) string, clock func() time.Time, load func() (coxgo.Table, error)) (stdout, stderr string, code int, handled bool) {
	if isUsageAssess(args) {
		return usageAssess(args[2:], getenv, clock)
	}
	if !wantsHelp(args) {
		return "", "", 0, false
	}
	table, err := load()
	if err != nil {
		return "", "", 0, false
	}
	return route(table, args, columnsFrom(getenv("COLUMNS")))
}

// execPython replaces this process with the Python cox, same argv, stdin, stdout and exit code.
// It returns only by exiting, with 2, when there is no target or the exec fails.
func execPython(args []string) {
	target, err := pythonTarget(os.Getenv, exec.LookPath, selfPath())
	if err != nil {
		fmt.Fprint(os.Stderr, notPorted(args))
		os.Exit(2)
	}
	execErr := syscall.Exec(target, append([]string{"cox"}, args...), os.Environ())
	fmt.Fprintf(os.Stderr, "cox-go: exec %s: %v\n", target, execErr)
	os.Exit(2)
}

func main() {
	args := os.Args[1:]
	stdout, stderr, code, handled := native(args, os.Getenv, time.Now, coxgo.Load)
	if !handled {
		execPython(args)
	}
	fmt.Print(stdout)
	fmt.Fprint(os.Stderr, stderr)
	os.Exit(code)
}
