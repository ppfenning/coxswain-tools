package main

import (
	"strings"
	"testing"

	coxgo "github.com/ppfenning/coxswain-tools/go"
)

func TestRouteKeepsTheNotPortedLineOnStdoutWithExitTwo(t *testing.T) {
	table := coxgo.Table{Groups: []coxgo.Group{{Name: "a"}, {Name: "b"}}}
	stdout, stderr, code := route(table, []string{"runs"}, 100)
	if stdout != "cox-go: 2 groups; nothing is ported yet\n" || stderr != "" || code != 2 {
		t.Errorf("got stdout %q, stderr %q, code %d", stdout, stderr, code)
	}
}

func TestRoutePrintsGroupHelpOnStdoutAndAnUnknownGroupOnStderr(t *testing.T) {
	table := coxgo.Table{Prog: "cox", Groups: []coxgo.Group{{Name: "g"}}}
	stdout, stderr, code := route(table, []string{"g", "--help"}, 100)
	if !strings.HasPrefix(stdout, "usage: cox g [-h]") || stderr != "" || code != 0 {
		t.Errorf("help: got stdout %q, stderr %q, code %d", stdout, stderr, code)
	}
	stdout, stderr, code = route(table, []string{"nope", "-h"}, 100)
	if stdout != "" || !strings.Contains(stderr, `unknown group "nope"`) || code != 2 {
		t.Errorf("unknown: got stdout %q, stderr %q, code %d", stdout, stderr, code)
	}
}

func TestColumnsFromDefaultsToEightyOnUnsetOrInvalid(t *testing.T) {
	if columnsFrom("") != 80 || columnsFrom("x") != 80 || columnsFrom("0") != 80 || columnsFrom("100") != 100 {
		t.Error("columnsFrom did not follow the COLUMNS rule")
	}
}
