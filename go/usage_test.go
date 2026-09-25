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
			r := Assess(filepath.Join(dir, "runs"), filepath.Join(dir, "profile.yaml"), now, nil)
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

// route._KNOWN_KEYS includes `forge`; a stale key list would drop both ceilings on a real profile.
func TestParseProfileKeepsTheCeilingsBesideAForgeKey(t *testing.T) {
	c, err := parseProfile("team: x\nforge: github\nspend:\n  window_ceiling_usd: 20\n  weekly_ceiling_usd: 200\n")
	if err != nil || c.Window == nil || *c.Window != 20 || c.Weekly == nil || *c.Weekly != 200 {
		t.Fatalf("got %+v, %v", c, err)
	}
}

// Python: window_from(json.loads(text), [], now, 5.0, 50.0) gives start 09:00Z, end 14:00Z,
// spent_usd 38.0, burn_usd_per_hour 7.6 for this text; the first two blocks are skipped.
const ccusageActive = `{"blocks":[` +
	`{"isActive":false,"startTime":"2026-09-25T04:00:00.000Z","endTime":"2026-09-25T09:00:00.000Z","costUSD":99},` +
	`{"isActive":true,"startTime":"nope","endTime":"2026-09-25T14:00:00.000Z","costUSD":1},` +
	`{"isActive":true,"startTime":"2026-09-25T09:00:00.000Z","endTime":"2026-09-25T14:00:00.000Z","costUSD":38}]}`

func TestActiveBlockGivesTheWindowPythonGivesFromTheSameText(t *testing.T) {
	w, ok := activeBlock([]byte(ccusageActive))
	start := time.Date(2026, 9, 25, 9, 0, 0, 0, time.UTC)
	if !ok || !w.Start.Equal(start) || !w.End.Equal(start.Add(5*time.Hour)) || w.Spent != 38 || w.Burn != 7.6 || w.Ceiling != nil {
		t.Fatalf("got %+v, %v", w, ok)
	}
}

func TestActiveBlockFindsNothingWhereJustPythonFindsNothing(t *testing.T) {
	for _, text := range []string{
		"", "not json", "[]", "{}", `{"blocks":null}`,
		`{"blocks":[{"isActive":false,"startTime":"2026-09-25T09:00:00Z","endTime":"2026-09-25T14:00:00Z","costUSD":1}]}`,
		`{"blocks":[{"startTime":"2026-09-25T09:00:00Z","endTime":"2026-09-25T14:00:00Z","costUSD":1}]}`,
		`{"blocks":[{"isActive":true,"startTime":"2026-09-25T09:00:00Z","endTime":"2026-09-25T14:00:00Z"}]}`,
		`{"blocks":[{"isActive":true,"startTime":"2026-09-25T09:00:00Z","endTime":"2026-09-25T14:00:00Z","costUSD":null}]}`,
		`{"blocks":["x"]}`,
	} {
		if w, ok := activeBlock([]byte(text)); ok {
			t.Errorf("%q gave %+v", text, w)
		}
	}
}

func TestAssessTakesTheActiveBlockAndTheProfilesSpendCeilings(t *testing.T) {
	dir := t.TempDir()
	profile := filepath.Join(dir, "profile.yaml")
	if err := os.WriteFile(profile, []byte("team: x\nspend:\n  window_ceiling_usd: 50\n  weekly_ceiling_usd: 907\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 9, 25, 10, 0, 0, 0, time.UTC)
	r := Assess(dir, profile, now, []byte(ccusageActive))
	want := "stop: spent 76% of ceiling at 20% elapsed; both ladders exhausted; weekly 0% of weekly ceiling"
	if r.Line() != want || r.Code != 4 {
		t.Fatalf("got %q, exit %d", r.Line(), r.Code)
	}
	if r := Assess(dir, profile, now, []byte("not json")); r.Code != 0 || !strings.Contains(r.Reason, "spent 0% of ceiling") {
		t.Fatalf("no block should fall back to the usage files: %q", r.Line())
	}
}

func TestCcusageBlocksIsSkippedByCoxNoCcusage(t *testing.T) {
	t.Setenv("PATH", t.TempDir())
	if got := CcusageBlocks(func(string) string { return "1" }); got != nil {
		t.Fatalf("got %q", got)
	}
	if got := CcusageBlocks(func(string) string { return "" }); got != nil {
		t.Fatalf("a missing npx should give nil, got %q", got)
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
