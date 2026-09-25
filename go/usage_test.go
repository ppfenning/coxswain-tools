package coxgo

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestAssessMatchesExpected(t *testing.T) {
	cases, err := filepath.Glob("testdata/usage-assess/*")
	if err != nil || len(cases) < 3 {
		t.Fatalf("found %d cases, err %v", len(cases), err)
	}
	for _, dir := range cases {
		t.Run(filepath.Base(dir), func(t *testing.T) {
			nowText, err := os.ReadFile(filepath.Join(dir, "now"))
			if err != nil {
				t.Fatal(err)
			}
			now, err := time.Parse(time.RFC3339, strings.TrimSpace(string(nowText)))
			if err != nil {
				t.Fatal(err)
			}
			want, err := os.ReadFile(filepath.Join(dir, "expected.txt"))
			if err != nil {
				t.Fatal(err)
			}
			r := Assess(filepath.Join(dir, "runs"), filepath.Join(dir, "profile.yaml"), now)
			if got := fmt.Sprintf("%s\nexit %d\n", r.Line(), r.Code); got != string(want) {
				t.Fatalf("got %q, want %q", got, want)
			}
		})
	}
}

func TestParseProfileReadsBothCeilingsAndRejectsWhatPythonRejects(t *testing.T) {
	c, err := parseProfile("team: x\nspend:\n  window_ceiling_usd: 20  # five hours\n  weekly_ceiling_usd: 200\n  node_cap_usd: 3\n")
	if err != nil || c.Window == nil || *c.Window != 20 || c.Weekly == nil || *c.Weekly != 200 {
		t.Fatalf("got %+v, %v", c, err)
	}
	for _, bad := range []string{"bogus: 1\n", "  window_ceiling_usd: 1\n", "spend:\n  other: 1\n", "spend:\n  window_ceiling_usd: x\n", "spend: 5\n", "no colon\n"} {
		if _, err := parseProfile(bad); err == nil {
			t.Errorf("%q parsed", bad)
		}
	}
}

func TestLoadPolicyFillsMissingKeysFromTheDefault(t *testing.T) {
	def := defaultPolicy()
	if got := loadPolicy([]byte("{}")); got.HardStop != def.HardStop || len(got.TierLadder) != 3 {
		t.Fatalf("empty object: %+v", got)
	}
	if got := loadPolicy([]byte(`{"min_headroom_usd": 5}`)); got.MinHeadroom != 5 || got.WeeklyHardStop != def.WeeklyHardStop {
		t.Fatalf("one key: %+v", got)
	}
	if got := loadPolicy([]byte("[1]")); got.HardStop != def.HardStop {
		t.Fatalf("not a mapping: %+v", got)
	}
}

// The fixtures run the usage-file path, where elapsed is always 100%. This literal
// window, 6% elapsed, reaches the branch the ticket quotes.
func TestAssessWordsAWindowThatIsPacedTooEarlyToJudge(t *testing.T) {
	now := time.Date(2026, 9, 25, 12, 0, 0, 0, time.UTC)
	ceiling, weeklyCeiling := 100.0, 100.0
	w := Window{Start: now.Add(-18 * time.Minute), End: now.Add(282 * time.Minute), Spent: 24, Ceiling: &ceiling, Burn: 1}
	wk := Window{Spent: 51, Ceiling: &weeklyCeiling}
	verdict, reason := assess(w, wk, defaultPolicy(), now)
	want := "spent 24% of ceiling at 6% elapsed; pace not judged before 10% elapsed; weekly 51% of weekly ceiling"
	if verdict != "go" || reason != want {
		t.Fatalf("got %s: %s", verdict, reason)
	}
}
