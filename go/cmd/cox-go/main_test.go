package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

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

func TestUsageAssessPrintsTheExpectedLineAndExitsWithItsCodeForEveryCase(t *testing.T) {
	cases, _ := filepath.Glob("../../testdata/usage-assess/*")
	if len(cases) < 3 {
		t.Fatalf("found %d cases", len(cases))
	}
	for _, dir := range cases {
		now, _ := os.ReadFile(filepath.Join(dir, "now"))
		want, _ := os.ReadFile(filepath.Join(dir, "expected.txt"))
		env := map[string]string{"COX_NOW": strings.TrimSpace(string(now))}
		args := []string{"--runs-dir", filepath.Join(dir, "runs"), "--profile=" + filepath.Join(dir, "profile.yaml")}
		stdout, stderr, code := usageAssess(args, func(k string) string { return env[k] }, time.Now)
		if got := fmt.Sprintf("%sexit %d\n", stdout, code); got != string(want) || stderr != "" {
			t.Errorf("%s: got %q (stderr %q), want %q", filepath.Base(dir), got, stderr, want)
		}
	}
}

func TestUsageAssessJSONIsNotPortedAndABadCoxNowIsAnError(t *testing.T) {
	none := func(string) string { return "" }
	if stdout, _, code := usageAssess([]string{"--json"}, none, time.Now); code != 2 || !strings.Contains(stdout, "not ported") {
		t.Errorf("--json: got %q, code %d", stdout, code)
	}
	bad := func(string) string { return "yesterday" }
	if stdout, stderr, code := usageAssess(nil, bad, time.Now); code != 2 || stdout != "" || !strings.Contains(stderr, "COX_NOW") {
		t.Errorf("COX_NOW: got stdout %q, stderr %q, code %d", stdout, stderr, code)
	}
}

func TestOnlyAUsageAssessThatIsNotAlonePlusHelpIsRoutedToTheCommand(t *testing.T) {
	if !isUsageAssess([]string{"usage", "assess"}) || !isUsageAssess([]string{"usage", "assess", "--json"}) ||
		isUsageAssess([]string{"usage", "assess", "-h"}) || isUsageAssess([]string{"usage", "--help"}) {
		t.Error("isUsageAssess routed the wrong argv")
	}
}

func TestColumnsFromDefaultsToEightyOnUnsetOrInvalid(t *testing.T) {
	if columnsFrom("") != 80 || columnsFrom("x") != 80 || columnsFrom("0") != 80 || columnsFrom("100") != 100 {
		t.Error("columnsFrom did not follow the COLUMNS rule")
	}
}
