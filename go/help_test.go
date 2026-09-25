package coxgo

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

const (
	fixtureDir   = "../tests/fixtures/help"
	fixtureWidth = 98 // COLUMNS=100, minus 2 as argparse does
)

func loadTable(t *testing.T) Table {
	t.Helper()
	table, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	return table
}

func fixture(t *testing.T, group, stem string) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(fixtureDir, group, stem+".txt"))
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestGroupHelpRunsMatchesItsFixture(t *testing.T) {
	got, err := GroupHelp(loadTable(t), "runs", fixtureWidth)
	if err != nil {
		t.Fatal(err)
	}
	if want := fixture(t, "runs", "runs"); got != want {
		t.Errorf("runs help differs\n--- got\n%s\n--- want\n%s", got, want)
	}
}

func TestCommandHelpRouteStatusMatchesItsFixture(t *testing.T) {
	got, err := CommandHelp(loadTable(t), "route", "status", fixtureWidth)
	if err != nil {
		t.Fatal(err)
	}
	if want := fixture(t, "route", "route-status"); got != want {
		t.Errorf("route status help differs\n--- got\n%s\n--- want\n%s", got, want)
	}
}

func TestCommandHelpRouteChairMatchesItsFixture(t *testing.T) {
	got, err := CommandHelp(loadTable(t), "route", "chair", fixtureWidth)
	if err != nil {
		t.Fatal(err)
	}
	if want := fixture(t, "route", "route-chair"); got != want {
		t.Errorf("route chair help differs\n--- got\n%s\n--- want\n%s", got, want)
	}
}

func TestCommandHelpRouteLaunchMatchesItsFixture(t *testing.T) {
	got, err := CommandHelp(loadTable(t), "route", "launch", fixtureWidth)
	if err != nil {
		t.Fatal(err)
	}
	if want := fixture(t, "route", "route-launch"); got != want {
		t.Errorf("route launch help differs\n--- got\n%s\n--- want\n%s", got, want)
	}
}

func TestSubcommandHelpRendersTheSubsOwnParser(t *testing.T) {
	got, err := SubcommandHelp(loadTable(t), "route", "chair", "take", fixtureWidth)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(got, "usage: cox route chair take [-h]") || !strings.Contains(got, "options:") {
		t.Errorf("got %q", got)
	}
}

func TestCommandHelpUsageAssessMatchesItsFixture(t *testing.T) {
	got, err := CommandHelp(loadTable(t), "usage", "assess", fixtureWidth)
	if err != nil {
		t.Fatal(err)
	}
	if want := fixture(t, "usage", "usage-assess"); got != want {
		t.Errorf("usage assess help differs\n--- got\n%s\n--- want\n%s", got, want)
	}
}

func TestUnknownGroupAndCommandAreErrors(t *testing.T) {
	table := loadTable(t)
	if _, err := GroupHelp(table, "nope", fixtureWidth); err == nil {
		t.Error("GroupHelp accepted an unknown group")
	}
	if _, err := CommandHelp(table, "runs", "nope", fixtureWidth); err == nil {
		t.Error("CommandHelp accepted an unknown command")
	}
	if _, err := SubcommandHelp(table, "route", "chair", "nope", fixtureWidth); err == nil {
		t.Error("SubcommandHelp accepted an unknown subcommand")
	}
	if _, err := SubcommandHelp(table, "route", "status", "x", fixtureWidth); err == nil {
		t.Error("SubcommandHelp accepted a command with no subcommands")
	}
	if _, err := SubcommandHelp(table, "nope", "chair", "take", fixtureWidth); err == nil {
		t.Error("SubcommandHelp accepted an unknown group")
	}
}

func TestSplitLinesBreaksAfterAHyphenInsideAWord(t *testing.T) {
	text := "the routing profile naming the window ceiling (default: ~/.config/agent-tools/profile.yaml or $AGENT_TOOLS_PROFILE)"
	want := []string{
		"the routing profile naming the window ceiling (default: ~/.config/agent-",
		"tools/profile.yaml or $AGENT_TOOLS_PROFILE)",
	}
	if got := splitLines(text, 74); !reflect.DeepEqual(got, want) {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestDescriptionAndEpilogExpandProg(t *testing.T) {
	table := Table{Prog: "cox", Groups: []Group{{Name: "g", Description: "%(prog)s does 100%%", Epilog: "see %(prog)s"}}}
	got, err := GroupHelp(table, "g", fixtureWidth)
	if err != nil {
		t.Fatal(err)
	}
	want := "usage: cox g [-h]\n\ncox g does 100%\n\noptions:\n  -h, --help  show this help message and exit\n\nsee cox g\n"
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

// TestEveryFixtureWalk reports how many fixtures the port already matches. It fails for none of
// them: the golden tests above own the bar, this one is the phase 2 backlog.
func TestEveryFixtureWalk(t *testing.T) {
	table := loadTable(t)
	files, err := filepath.Glob(filepath.Join(fixtureDir, "*", "*.txt"))
	if err != nil {
		t.Fatal(err)
	}
	matched := 0
	var misses []string
	for _, f := range files {
		group := filepath.Base(filepath.Dir(f))
		stem := strings.TrimSuffix(filepath.Base(f), ".txt")
		want, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		got, err := renderFixture(table, group, stem)
		if err != nil || got != string(want) {
			misses = append(misses, group+"/"+stem)
			continue
		}
		matched++
	}
	t.Logf("matched %d of %d fixtures", matched, len(files))
	for _, m := range misses {
		t.Logf("no match: %s", m)
	}
}

// renderFixture picks GroupHelp for `<group>.txt` and the command whose name completes
// `<group>-<command>.txt`; a hyphenated command name is matched whole, never split.
func renderFixture(table Table, group, stem string) (string, error) {
	if stem == group {
		return GroupHelp(table, group, fixtureWidth)
	}
	return CommandHelp(table, group, strings.TrimPrefix(stem, group+"-"), fixtureWidth)
}
