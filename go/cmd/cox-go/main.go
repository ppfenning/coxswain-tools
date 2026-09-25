// cox-go is the phase 1 spike of the Go CLI; the Python `cox` stays the shipped CLI.
package main

import (
	"bytes"
	"cmp"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
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

// route turns argv into what goes to stdout, what goes to stderr, and the exit code.
// The not-ported line stays on stdout, where it was before help existed.
func route(table coxgo.Table, args []string, columns int) (stdout, stderr string, code int) {
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
		return fmt.Sprintf("cox-go: %d groups; nothing is ported yet\n", len(table.Groups)), "", 2
	}
	if err != nil {
		return "", fmt.Sprintf("cox-go: %v\n", err), 2
	}
	return help, "", 0
}

// isUsageAssess is true for `usage assess` with anything but a lone help flag, which still routes to help.
func isUsageAssess(args []string) bool {
	return len(args) >= 2 && args[0] == "usage" && args[1] == "assess" && !(len(args) == 3 && isHelp(args[2]))
}

// usageAssess prints `<verdict>: <reason>` and exits with the verdict's code. The clock and
// environment come in as arguments; COX_NOW, an RFC 3339 UTC timestamp, overrides the clock,
// and COX_NO_CCUSAGE=1 skips the ccusage call.
func usageAssess(args []string, getenv func(string) string, clock func() time.Time) (stdout, stderr string, code int) {
	var usage bytes.Buffer
	fs := flag.NewFlagSet("usage assess", flag.ContinueOnError)
	fs.SetOutput(&usage)
	asJSON := fs.Bool("json", false, "")
	runsDir := fs.String("runs-dir", "runs", "")
	profile := fs.String("profile", "", "")
	if err := fs.Parse(args); err != nil {
		return "", fmt.Sprintf("cox-go: usage assess: %v\n%s", err, usage.String()), 2
	}
	if *asJSON {
		return "cox-go: usage assess --json is not ported\n", "", 2
	}
	now := clock().UTC()
	if v := getenv("COX_NOW"); v != "" {
		parsed, err := time.Parse(time.RFC3339, v)
		if err != nil {
			return "", fmt.Sprintf("cox-go: COX_NOW %q: %v\n", v, err), 2
		}
		now = parsed
	}
	path := cmp.Or(*profile, getenv("AGENT_TOOLS_PROFILE"), "~/.config/agent-tools/profile.yaml")
	if rest, ok := strings.CutPrefix(path, "~/"); ok {
		path = filepath.Join(getenv("HOME"), rest)
	}
	r := coxgo.Assess(*runsDir, path, now, coxgo.CachedBlocks(*runsDir, now, getenv, func() []byte { return coxgo.CcusageBlocks(getenv) }))
	return r.Line() + "\n", "", r.Code
}

func main() {
	var stdout, stderr string
	var code int
	if args := os.Args[1:]; isUsageAssess(args) {
		stdout, stderr, code = usageAssess(args[2:], os.Getenv, time.Now)
	} else {
		table, err := coxgo.Load()
		if err != nil {
			fmt.Fprintln(os.Stderr, "cox-go:", err)
			os.Exit(1)
		}
		stdout, stderr, code = route(table, args, columnsFrom(os.Getenv("COLUMNS")))
	}
	fmt.Print(stdout)
	fmt.Fprint(os.Stderr, stderr)
	os.Exit(code)
}
