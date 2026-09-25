// cox-go is the phase 1 spike of the Go CLI; the Python `cox` stays the shipped CLI.
package main

import (
	"fmt"
	"os"
	"strconv"

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
	default:
		return fmt.Sprintf("cox-go: %d groups; nothing is ported yet\n", len(table.Groups)), "", 2
	}
	if err != nil {
		return "", fmt.Sprintf("cox-go: %v\n", err), 2
	}
	return help, "", 0
}

func main() {
	table, err := coxgo.Load()
	if err != nil {
		fmt.Fprintln(os.Stderr, "cox-go:", err)
		os.Exit(1)
	}
	stdout, stderr, code := route(table, os.Args[1:], columnsFrom(os.Getenv("COLUMNS")))
	fmt.Print(stdout)
	fmt.Fprint(os.Stderr, stderr)
	os.Exit(code)
}
