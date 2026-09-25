package coxgo

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
	"unicode"
)

// Port of `cox usage assess` (agent_tools/usage_window.py, pacing.py, cli._usage_assess).
// The five-hour window is the block `ccusage blocks --active --json` marks active, else
// the runs dir's `*.usage.json` files. The Python edge also reads the run_store SQLite
// fallback; the Go port does not, so it matches Python only where the runs dir holds
// `*.usage.json` files.

// Window is a spend window. A nil Ceiling means none was supplied.
type Window struct {
	Start, End time.Time
	Spent      float64
	Ceiling    *float64
	Burn       float64 // USD per hour
}

// Policy mirrors pacing.Policy. MinElapsed is not read from policy.pacing.json, as in Python.
type Policy struct {
	PaceThresholds []float64
	TierLadder     []string
	EffortLadder   []string
	MinHeadroom    float64
	HardStop       float64
	WeeklyHardStop float64
	MinElapsed     float64
}

// Result is the verdict, its reason and the process exit code.
type Result struct {
	Verdict string
	Reason  string
	Code    int
}

// Line is the one line `usage assess` prints.
func (r Result) Line() string { return r.Verdict + ": " + r.Reason }

// Ceilings are the profile's optional spend ceilings.
type Ceilings struct{ Window, Weekly *float64 }

type usageRun struct {
	Started time.Time
	Cost    float64
}

// defaultPolicy is usage_window.DEFAULT_POLICY.
func defaultPolicy() Policy {
	return Policy{
		PaceThresholds: []float64{1.2, 1.5, 2.0, 2.5},
		TierLadder:     []string{"deep", "standard", "cheap"},
		EffortLadder:   []string{"high", "low"},
		MinHeadroom:    0.0,
		HardStop:       0.99,
		WeeklyHardStop: 0.93,
		MinElapsed:     0.10,
	}
}

// exitCode is cli._USAGE_ASSESS_EXIT.
func exitCode(verdict string) int {
	switch verdict {
	case "go", "go_degraded":
		return 0
	case "hold":
		return 3
	default:
		return 4
	}
}

func pick[T any](m map[string]json.RawMessage, key string, def T) T {
	var v T
	if raw, ok := m[key]; ok && json.Unmarshal(raw, &v) == nil {
		return v
	}
	return def
}

// loadPolicy is cli._resolved_pacing_policy on the file's bytes: a key that is
// missing or has the wrong type falls back to the default. Python raises on a
// wrong type; an empty ladder would index out of range there, so it falls back here.
func loadPolicy(raw []byte) Policy {
	def := defaultPolicy()
	var m map[string]json.RawMessage
	if json.Unmarshal(raw, &m) != nil || m == nil {
		return def
	}
	p := Policy{
		PaceThresholds: pick(m, "pace_thresholds", def.PaceThresholds),
		TierLadder:     pick(m, "tier_ladder", def.TierLadder),
		EffortLadder:   pick(m, "effort_ladder", def.EffortLadder),
		MinHeadroom:    pick(m, "min_headroom_usd", def.MinHeadroom),
		HardStop:       pick(m, "hard_stop_fraction", def.HardStop),
		WeeklyHardStop: pick(m, "weekly_hard_stop_fraction", def.WeeklyHardStop),
		MinElapsed:     def.MinElapsed,
	}
	if len(p.TierLadder) == 0 {
		p.TierLadder = def.TierLadder
	}
	if len(p.EffortLadder) == 0 {
		p.EffortLadder = def.EffortLadder
	}
	return p
}

func stripComment(line string) string {
	for i := 1; i < len(line); i++ {
		if line[i] == '#' && strings.ContainsRune(" \t\r\n\v\f", rune(line[i-1])) {
			return strings.TrimRightFunc(line[:i], unicode.IsSpace)
		}
	}
	return line
}

func knownKey(key string) bool {
	switch key {
	case "team", "cartridges_dir", "skills_roots", "provider_profile", "harness_dir",
		"workspace_dir", "assume", "router", "sources", "repo_map", "forge":
		return true
	default:
		return false
	}
}

// parseProfile is route.parse_profile narrowed to the two ceilings. Any line
// Python rejects is an error here too, so the caller drops both ceilings as cli does.
func parseProfile(text string) (Ceilings, error) {
	var out Ceilings
	inSpend := false
	for i, raw := range strings.Split(text, "\n") {
		line := strings.TrimSuffix(raw, "\r")
		bad := fmt.Errorf("line %d: %s", i+1, raw)
		if strings.TrimSpace(line) == "" || strings.HasPrefix(strings.TrimLeftFunc(line, unicode.IsSpace), "#") {
			continue
		}
		key, value, hasColon := strings.Cut(stripComment(line), ":")
		key, value = strings.TrimSpace(key), strings.TrimSpace(value)
		if line != strings.TrimLeftFunc(line, unicode.IsSpace) {
			if !inSpend || !hasColon {
				return Ceilings{}, bad
			}
			n, err := strconv.ParseFloat(value, 64)
			switch {
			case err != nil:
				return Ceilings{}, bad
			case key == "window_ceiling_usd":
				out.Window = &n
			case key == "weekly_ceiling_usd":
				out.Weekly = &n
			case key != "node_cap_usd":
				return Ceilings{}, bad
			}
			continue
		}
		inSpend = false
		switch {
		case !hasColon:
			return Ceilings{}, bad
		case key == "spend" && value != "":
			return Ceilings{}, bad
		case key == "spend":
			inSpend = true
		case !knownKey(key):
			return Ceilings{}, bad
		case (key == "sources" || key == "repo_map") && !json.Valid([]byte(value)):
			return Ceilings{}, bad
		}
	}
	return out, nil
}

func number(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case string:
		f, _ := strconv.ParseFloat(n, 64)
		return f
	default:
		return 0
	}
}

// usageOf reads one `<run>.usage.json`: cost from summary.cost_usd, else a
// top-level cost_usd, else 0; start from summary.started_at, else the file's mtime.
func usageOf(data []byte, mtime time.Time) (usageRun, bool) {
	var u map[string]any
	if json.Unmarshal(data, &u) != nil || u == nil {
		return usageRun{}, false
	}
	summary, _ := u["summary"].(map[string]any)
	cost := number(summary["cost_usd"])
	if cost == 0 {
		cost = number(u["cost_usd"])
	}
	started := mtime
	if s, _ := summary["started_at"].(string); s != "" {
		if t, err := time.Parse(time.RFC3339, s); err == nil {
			started = t
		}
	}
	return usageRun{Started: started, Cost: cost}, true
}

// windowFrom is the fallback branch of usage_window.window_from and weekly_window_from:
// the runs that started in [now-span, now], summed.
func windowFrom(runs []usageRun, now time.Time, span time.Duration, ceiling *float64) Window {
	start := now.Add(-span)
	spent := 0.0
	for _, r := range runs {
		if !r.Started.Before(start) && !r.Started.After(now) {
			spent += r.Cost
		}
	}
	return Window{Start: start, End: now, Spent: spent, Ceiling: ceiling, Burn: spent / math.Max(span.Seconds()/3600, 1e-9)}
}

// truthy is Python's bool() on a decoded JSON value.
func truthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case float64:
		return x != 0
	case string:
		return x != ""
	case []any:
		return len(x) > 0
	case map[string]any:
		return len(x) > 0
	default:
		return true
	}
}

// pyFloat is Python's float() on a decoded JSON value; a null, list or object does not convert.
func pyFloat(v any) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case bool:
		return map[bool]float64{true: 1, false: 0}[x], true
	case string:
		f, err := strconv.ParseFloat(strings.TrimSpace(x), 64)
		return f, err == nil
	default:
		return 0, false
	}
}

func blockTime(v any) (time.Time, bool) {
	s, ok := v.(string)
	if !ok {
		return time.Time{}, false
	}
	t, err := time.Parse(time.RFC3339Nano, s)
	return t, err == nil
}

// activeBlock is usage_window._active_block folded into window_from: the first block ccusage
// marks active whose startTime, endTime and costUSD all parse. A block that does not parse
// is skipped. The ceiling is the caller's.
func activeBlock(text []byte) (Window, bool) {
	var top struct {
		Blocks []any `json:"blocks"`
	}
	if json.Unmarshal(text, &top) != nil {
		return Window{}, false
	}
	for _, b := range top.Blocks {
		block, ok := b.(map[string]any)
		if !ok || !truthy(block["isActive"]) {
			continue
		}
		start, okStart := blockTime(block["startTime"])
		end, okEnd := blockTime(block["endTime"])
		spent, okSpent := pyFloat(block["costUSD"])
		if !okStart || !okEnd || !okSpent {
			continue
		}
		return Window{Start: start, End: end, Spent: spent, Burn: spent / math.Max(end.Sub(start).Hours(), 1e-9)}, true
	}
	return Window{}, false
}

// ccusageArgv is the argument list usage_window.gather runs after `npx`.
var ccusageArgv = []string{"-y", "ccusage@latest", "blocks", "--active", "--json"}

// CcusageBlocks is the edge: the stdout of `npx -y ccusage@latest blocks --active --json`, or
// nil when COX_NO_CCUSAGE is 1, npx is missing, the call times out at 30s, or it exits nonzero.
func CcusageBlocks(getenv func(string) string) []byte {
	if getenv("COX_NO_CCUSAGE") == "1" {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "npx", ccusageArgv...)
	cmd.WaitDelay = 2 * time.Second
	out, err := cmd.Output()
	if err != nil {
		return nil
	}
	return out
}

// ccusageCacheFile and ccusageCacheTTL are usage_window._CCUSAGE_CACHE_FILE and its 60 s window.
const (
	ccusageCacheFile = ".ccusage-block.json"
	ccusageCacheTTL  = 60 * time.Second
)

func isObject(b []byte) bool {
	var m map[string]json.RawMessage
	return json.Unmarshal(b, &m) == nil && m != nil
}

type cacheFile struct {
	At     string          `json:"at"`
	Blocks json.RawMessage `json:"blocks"`
}

// cacheFresh is usage_window._cached_blocks: the blocks of a cache file whose `at` carries an
// offset and lies in [now-60s, now]. A naive or future `at`, or a non-object `blocks`, is absent.
func cacheFresh(file []byte, now time.Time) ([]byte, bool) {
	var c cacheFile
	if json.Unmarshal(file, &c) != nil {
		return nil, false
	}
	at, err := time.Parse(time.RFC3339Nano, c.At)
	if age := now.Sub(at); err != nil || age < 0 || age >= ccusageCacheTTL || !isObject(c.Blocks) {
		return nil, false
	}
	return c.Blocks, true
}

// cacheEntry is the file Python's datetime.fromisoformat reads: `at` with a numeric offset.
func cacheEntry(blocks []byte, now time.Time) ([]byte, bool) {
	if !isObject(blocks) {
		return nil, false
	}
	out, err := json.Marshal(cacheFile{At: now.Format("2006-01-02T15:04:05.000000-07:00"), Blocks: blocks})
	return out, err == nil
}

// CachedBlocks is the ccusage edge behind the one-minute cache shared with Python. COX_NO_CCUSAGE=1
// gives nil and touches no file. Only a run whose output is a JSON object is written, and a failed
// write is ignored.
func CachedBlocks(runsDir string, now time.Time, getenv func(string) string, run func() []byte) []byte {
	if getenv("COX_NO_CCUSAGE") == "1" {
		return nil
	}
	path := filepath.Join(runsDir, ccusageCacheFile)
	if file, err := os.ReadFile(path); err == nil {
		if blocks, ok := cacheFresh(file, now); ok {
			return blocks
		}
	}
	out := run()
	if entry, ok := cacheEntry(out, now); ok {
		_ = os.WriteFile(path, entry, 0o644)
	}
	return out
}

func pct(x float64) string { return fmt.Sprintf("%.0f%%", x*100) }

func measured(w Window) bool { return w.Ceiling != nil && *w.Ceiling > 0 }

func elapsedFraction(w Window, now time.Time) float64 {
	span := w.End.Sub(w.Start).Seconds()
	if span <= 0 {
		if now.Before(w.End) {
			return 0
		}
		return 1
	}
	return min(1, max(0, now.Sub(w.Start).Seconds()/span))
}

func projectedTotal(w Window, now time.Time) float64 {
	return w.Spent + w.Burn*max(0, w.End.Sub(now).Seconds()/3600)
}

func ratio(spent, elapsed float64) float64 {
	switch {
	case elapsed > 0:
		return spent / elapsed
	case spent > 0:
		return math.Inf(1)
	default:
		return 0
	}
}

// ladderCeilings walks `rung` steps down the tier ladder first, then the effort ladder.
func ladderCeilings(rung int, p Policy) (tier, effort string) {
	tierSteps := len(p.TierLadder) - 1
	return p.TierLadder[min(rung, tierSteps)], p.EffortLadder[min(max(rung-tierSteps, 0), len(p.EffortLadder)-1)]
}

// assess is pacing.assess: the verdict and reason a window, a weekly window and a policy make at now.
func assess(w, weekly Window, p Policy, now time.Time) (verdict, reason string) {
	elapsed := elapsedFraction(w, now)
	hasWeekly := measured(weekly)
	weeklyFraction := 0.0
	if hasWeekly {
		weeklyFraction = weekly.Spent / *weekly.Ceiling
	}
	if hasWeekly && weeklyFraction >= p.WeeklyHardStop {
		return "stop", fmt.Sprintf("weekly spend %s of weekly ceiling (hard stop at %s)", pct(weeklyFraction), pct(p.WeeklyHardStop))
	}
	verdict, reason = assessWindow(w, p, now, elapsed)
	if hasWeekly {
		reason = fmt.Sprintf("%s; weekly %s of weekly ceiling", reason, pct(weeklyFraction))
	}
	return verdict, reason
}

func assessWindow(w Window, p Policy, now time.Time, elapsed float64) (verdict, reason string) {
	if !measured(w) {
		return "go", "window is unmeasured: no usable ceiling_usd; reporting pace only"
	}
	spentFraction := w.Spent / *w.Ceiling
	headroom := *w.Ceiling - projectedTotal(w, now)
	if spentFraction >= p.HardStop {
		return "stop", fmt.Sprintf("spent %s of ceiling (hard stop at %s)", pct(spentFraction), pct(p.HardStop))
	}
	paceJudged := elapsed >= p.MinElapsed
	paceRatio := 0.0
	if paceJudged {
		paceRatio = ratio(spentFraction, elapsed)
	}
	rung := 0
	for _, t := range p.PaceThresholds {
		if paceRatio > t {
			rung++
		}
	}
	pace := fmt.Sprintf("spent %s of ceiling at %s elapsed", pct(spentFraction), pct(elapsed))
	switch tier, effort := ladderCeilings(rung, p); {
	case rung > (len(p.TierLadder)-1)+(len(p.EffortLadder)-1):
		return "stop", pace + "; both ladders exhausted"
	case headroom < p.MinHeadroom:
		return "hold", fmt.Sprintf("headroom $%.2f below minimum $%.2f; holding until the window ends", headroom, p.MinHeadroom)
	case rung == 0 && paceJudged:
		return "go", pace + "; on pace"
	case rung == 0:
		return "go", fmt.Sprintf("%s; pace not judged before %s elapsed", pace, pct(p.MinElapsed))
	default:
		return "go_degraded", fmt.Sprintf("%s; ceilings tier=%s effort=%s", pace, tier, effort)
	}
}

func readText(path string) (string, bool) {
	b, err := os.ReadFile(path)
	return string(b), err == nil
}

// readUsage is the edge: every parsing `*.usage.json` in runsDir, in name order.
func readUsage(runsDir string) []usageRun {
	entries, err := os.ReadDir(runsDir)
	if err != nil {
		return nil
	}
	var runs []usageRun
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".usage.json") {
			continue
		}
		path := filepath.Join(runsDir, e.Name())
		data, err := os.ReadFile(path)
		info, statErr := os.Stat(path)
		if err != nil || statErr != nil {
			continue
		}
		if run, ok := usageOf(data, info.ModTime()); ok {
			runs = append(runs, run)
		}
	}
	return runs
}

// Assess is `cox usage assess` for a runs dir and a profile at an explicit now. blocks is
// ccusage's output, nil when it gave none. A missing or unparsable profile means no ceilings,
// a missing policy file means the defaults.
func Assess(runsDir, profilePath string, now time.Time, blocks []byte) Result {
	var ceilings Ceilings
	if text, ok := readText(profilePath); ok {
		if c, err := parseProfile(text); err == nil {
			ceilings = c
		}
	}
	policy := defaultPolicy()
	if text, ok := readText(filepath.Join(runsDir, "policy.pacing.json")); ok {
		policy = loadPolicy([]byte(text))
	}
	runs := readUsage(runsDir)
	window, ok := activeBlock(blocks)
	if ok {
		window.Ceiling = ceilings.Window
	} else {
		window = windowFrom(runs, now, 5*time.Hour, ceilings.Window)
	}
	weekly := windowFrom(runs, now, 7*24*time.Hour, ceilings.Weekly)
	verdict, reason := assess(window, weekly, policy, now)
	return Result{Verdict: verdict, Reason: reason, Code: exitCode(verdict)}
}
