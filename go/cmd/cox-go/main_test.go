package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	coxgo "github.com/ppfenning/coxswain-tools/go"
)

func TestRouteLeavesANonHelpArgvUnhandledForThePythonFallback(t *testing.T) {
	table := coxgo.Table{Groups: []coxgo.Group{{Name: "a"}, {Name: "b"}}}
	for _, args := range [][]string{nil, {"runs"}, {"runs", "series", "--json"}} {
		stdout, stderr, code, handled := route(table, args, 100)
		if stdout != "" || stderr != "" || code != 0 || handled {
			t.Errorf("%q: got stdout %q, stderr %q, code %d, handled %v", args, stdout, stderr, code, handled)
		}
	}
}

func TestRoutePrintsGroupHelpOnStdoutAndAnUnknownGroupOnStderr(t *testing.T) {
	table := coxgo.Table{Prog: "cox", Groups: []coxgo.Group{{Name: "g"}}}
	stdout, stderr, code, handled := route(table, []string{"g", "--help"}, 100)
	if !strings.HasPrefix(stdout, "usage: cox g [-h]") || stderr != "" || code != 0 || !handled {
		t.Errorf("help: got stdout %q, stderr %q, code %d", stdout, stderr, code)
	}
	stdout, stderr, code, handled = route(table, []string{"nope", "-h"}, 100)
	if stdout != "" || !strings.Contains(stderr, `unknown group "nope"`) || code != 2 || !handled {
		t.Errorf("unknown: got stdout %q, stderr %q, code %d", stdout, stderr, code)
	}
}

func TestRoutePrintsSubcommandHelpAndAnUnknownSubOnStderr(t *testing.T) {
	table, err := coxgo.Load()
	if err != nil {
		t.Fatal(err)
	}
	stdout, stderr, code, handled := route(table, []string{"route", "chair", "take", "-h"}, 100)
	if !strings.HasPrefix(stdout, "usage: cox route chair take [-h]") || stderr != "" || code != 0 || !handled {
		t.Errorf("help: got stdout %q, stderr %q, code %d", stdout, stderr, code)
	}
	stdout, stderr, code, handled = route(table, []string{"route", "chair", "nope", "-h"}, 100)
	if stdout != "" || !strings.Contains(stderr, `unknown subcommand "nope"`) || code != 2 || !handled {
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
		env := map[string]string{"COX_NOW": strings.TrimSpace(string(now)), "COX_NO_CCUSAGE": "1"}
		args := []string{"--runs-dir", filepath.Join(dir, "runs"), "--profile=" + filepath.Join(dir, "profile.yaml")}
		stdout, stderr, code, handled := usageAssess(args, func(k string) string { return env[k] }, time.Now)
		if got := fmt.Sprintf("%sexit %d\n", stdout, code); got != string(want) || stderr != "" || !handled {
			t.Errorf("%s: got %q (stderr %q), want %q", filepath.Base(dir), got, stderr, want)
		}
	}
}

func TestUsageAssessLeavesJSONAndArgvGoRejectsToPythonAndABadCoxNowIsAnError(t *testing.T) {
	none := func(string) string { return "" }
	for _, args := range [][]string{{"--json"}, {"--runs", "x"}, {"extra"}} {
		if stdout, stderr, code, handled := usageAssess(args, none, time.Now); handled || stdout != "" || stderr != "" || code != 0 {
			t.Errorf("%q: got stdout %q, stderr %q, code %d, handled %v", args, stdout, stderr, code, handled)
		}
	}
	bad := func(string) string { return "yesterday" }
	if stdout, stderr, code, handled := usageAssess(nil, bad, time.Now); code != 2 || stdout != "" || !strings.Contains(stderr, "COX_NOW") || !handled {
		t.Errorf("COX_NOW: got stdout %q, stderr %q, code %d", stdout, stderr, code)
	}
}

func TestNativeLoadsTheTableOnlyForHelpAndHandsAFailedLoadToPython(t *testing.T) {
	none := func(string) string { return "" }
	unused := func() (coxgo.Table, error) { t.Fatal("load called for a non-help argv"); return coxgo.Table{}, nil }
	broken := func() (coxgo.Table, error) { return coxgo.Table{}, errors.New("broken table") }
	if _, _, _, handled := native([]string{"runs", "series"}, none, time.Now, unused); handled {
		t.Error("runs series: handled natively")
	}
	if _, _, _, handled := native([]string{"usage", "assess", "--json"}, none, time.Now, unused); handled {
		t.Error("usage assess --json: handled natively")
	}
	if _, _, _, handled := native([]string{"runs", "-h"}, none, time.Now, broken); handled {
		t.Error("help with a broken table: handled natively")
	}
	if stdout, _, code, handled := native([]string{"runs", "-h"}, none, time.Now, coxgo.Load); !handled || code != 0 || !strings.HasPrefix(stdout, "usage: cox runs") {
		t.Errorf("help: got stdout %q, code %d, handled %v", stdout, code, handled)
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
