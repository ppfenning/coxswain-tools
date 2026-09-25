package coxgo

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"slices"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf16"
)

// ProfileError is route.ProfileError: the message names the line number and the line as written.
type ProfileError struct {
	Line int
	Text string
}

func (e *ProfileError) Error() string { return fmt.Sprintf("line %d: %s", e.Line, e.Text) }

// profileKeys is route._KNOWN_KEYS; testdata/profile-keys.json holds Python's list and both test suites check it.
var profileKeys = []string{
	"assume", "cartridges_dir", "forge", "harness_dir", "provider_profile", "repo_map", "router",
	"skills_roots", "sources", "team", "tracker", "workspace_dir",
}

func knownKey(key string) bool { return slices.Contains(profileKeys, key) }

var spendKeys = []string{"window_ceiling_usd", "weekly_ceiling_usd", "node_cap_usd"}

// pySpace is str.isspace, which is what str.strip and the regex \s use. It is unicode.IsSpace
// plus \x1c to \x1f, which Go does not count as space.
func pySpace(r rune) bool { return unicode.IsSpace(r) || (0x1c <= r && r <= 0x1f) }

func pyStrip(s string) string { return strings.TrimFunc(s, pySpace) }

// stripComment is route._stripped_content: cut at the first # that follows a pySpace rune, then rstrip.
func stripComment(line string) string {
	prev := 'x'
	for i, r := range line {
		if r == '#' && pySpace(prev) {
			return strings.TrimRightFunc(line[:i], pySpace)
		}
		prev = r
	}
	return line
}

// profileLines is str.splitlines: it breaks on \n, \r\n, \r, \v, \f, \x1c, \x1d, \x1e, \x85,
// U+2028 and U+2029, and a trailing break adds no empty line.
func profileLines(text string) []string {
	var lines []string
	start := 0
	for i, r := range text {
		if i < start {
			continue
		}
		switch r {
		case '\n', '\r', '\v', '\f', 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029:
			lines = append(lines, text[start:i])
			start = i + len(string(r))
			if r == '\r' && strings.HasPrefix(text[start:], "\n") {
				start++
			}
		}
	}
	if start < len(text) {
		lines = append(lines, text[start:])
	}
	return lines
}

func isDigit(b byte) bool { return '0' <= b && b <= '9' }

// pyDecimal is Python's float(s) for a string: it accepts single underscores between
// digits, which strconv rejects, and rejects hex floats, which strconv accepts.
// unknown: Python also reads non-ASCII digits; those are rejected here.
func pyDecimal(s string) (float64, bool) {
	if strings.ContainsAny(s, "xXpP") {
		return 0, false
	}
	for i := 0; i < len(s); i++ {
		if s[i] == '_' && !(i > 0 && i+1 < len(s) && isDigit(s[i-1]) && isDigit(s[i+1])) {
			return 0, false
		}
	}
	f, err := strconv.ParseFloat(strings.ReplaceAll(s, "_", ""), 64)
	if err != nil && !errors.Is(err, strconv.ErrRange) {
		return 0, false
	}
	return f, true
}

// pyJSON is json.loads for one profile value. An integer keeps every digit as a json.Number;
// any other number is a float64, and NaN, Infinity, -Infinity and overflow are allowed, as in Python.
// The scanner moves one index forward, which is the idiom for a scanner; it stays local to this call.
func pyJSON(s string) (any, bool) {
	p := &jsonScan{s: s}
	p.space()
	v, ok := p.value()
	p.space()
	return v, ok && p.i == len(s)
}

type jsonScan struct {
	s string
	i int
}

func (p *jsonScan) space() {
	for p.i < len(p.s) && strings.IndexByte(" \t\n\r", p.s[p.i]) >= 0 {
		p.i++
	}
}

func (p *jsonScan) lit(word string) bool {
	if strings.HasPrefix(p.s[p.i:], word) {
		p.i += len(word)
		return true
	}
	return false
}

func (p *jsonScan) digitAt(j int) bool { return j < len(p.s) && isDigit(p.s[j]) }

func (p *jsonScan) value() (any, bool) {
	switch {
	case p.i >= len(p.s):
		return nil, false
	case p.s[p.i] == '{':
		return p.object()
	case p.s[p.i] == '[':
		return p.array()
	case p.s[p.i] == '"':
		return p.str()
	case p.lit("null"):
		return nil, true
	case p.lit("true"):
		return true, true
	case p.lit("false"):
		return false, true
	case p.lit("NaN"):
		return math.NaN(), true
	case p.lit("Infinity"):
		return math.Inf(1), true
	case p.lit("-Infinity"):
		return math.Inf(-1), true
	default:
		return p.number()
	}
}

// number is json's NUMBER_RE: -?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?, an int without a fraction or exponent.
func (p *jsonScan) number() (any, bool) {
	start := p.i
	p.lit("-")
	switch {
	case p.digitAt(p.i) && p.s[p.i] == '0':
		p.i++
	case p.digitAt(p.i):
		for p.digitAt(p.i) {
			p.i++
		}
	default:
		return nil, false
	}
	isFloat := false
	if p.i < len(p.s) && p.s[p.i] == '.' && p.digitAt(p.i+1) {
		isFloat = true
		for p.i++; p.digitAt(p.i); p.i++ {
		}
	}
	if p.i < len(p.s) && (p.s[p.i] == 'e' || p.s[p.i] == 'E') {
		j := p.i + 1
		if j < len(p.s) && (p.s[j] == '+' || p.s[j] == '-') {
			j++
		}
		if p.digitAt(j) {
			isFloat = true
			for p.i = j; p.digitAt(p.i); p.i++ {
			}
		}
	}
	text := p.s[start:p.i]
	if isFloat {
		f, err := strconv.ParseFloat(text, 64)
		return f, err == nil || errors.Is(err, strconv.ErrRange)
	}
	n, _ := new(big.Int).SetString(text, 10)
	return json.Number(n.String()), true
}

func (p *jsonScan) hex4(j int) (rune, bool) {
	if j+4 > len(p.s) {
		return 0, false
	}
	n, err := strconv.ParseUint(p.s[j:j+4], 16, 32)
	return rune(n), err == nil
}

var jsonEscapes = map[byte]byte{'"': '"', '\\': '\\', '/': '/', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}

// str is json's strict scanstring. unknown: a lone surrogate escape is a str Python can hold
// and a Go string cannot; it becomes U+FFFD here.
func (p *jsonScan) str() (any, bool) {
	var b strings.Builder
	for p.i++; p.i < len(p.s); {
		c := p.s[p.i]
		switch {
		case c == '"':
			p.i++
			return b.String(), true
		case c < 0x20 || (c == '\\' && p.i+1 >= len(p.s)):
			return nil, false
		case c != '\\':
			b.WriteByte(c)
			p.i++
		case jsonEscapes[p.s[p.i+1]] != 0:
			b.WriteByte(jsonEscapes[p.s[p.i+1]])
			p.i += 2
		case p.s[p.i+1] != 'u':
			return nil, false
		default:
			r, ok := p.hex4(p.i + 2)
			if !ok {
				return nil, false
			}
			p.i += 6
			if 0xd800 <= r && r < 0xdc00 && strings.HasPrefix(p.s[p.i:], `\u`) {
				if lo, ok := p.hex4(p.i + 2); ok && 0xdc00 <= lo && lo < 0xe000 {
					r = utf16.DecodeRune(r, lo)
					p.i += 6
				}
			}
			b.WriteRune(r)
		}
	}
	return nil, false
}

func (p *jsonScan) array() (any, bool) {
	out := []any{}
	p.i++
	p.space()
	if p.lit("]") {
		return out, true
	}
	for {
		p.space()
		v, ok := p.value()
		if !ok {
			return nil, false
		}
		out = append(out, v)
		p.space()
		switch {
		case p.lit("]"):
			return out, true
		case !p.lit(","):
			return nil, false
		}
	}
}

func (p *jsonScan) object() (any, bool) {
	out := map[string]any{}
	p.i++
	p.space()
	if p.lit("}") {
		return out, true
	}
	for {
		p.space()
		if p.i >= len(p.s) || p.s[p.i] != '"' {
			return nil, false
		}
		k, ok := p.str()
		if !ok {
			return nil, false
		}
		p.space()
		if !p.lit(":") {
			return nil, false
		}
		p.space()
		v, ok := p.value()
		if !ok {
			return nil, false
		}
		out[k.(string)] = v
		p.space()
		switch {
		case p.lit("}"):
			return out, true
		case !p.lit(","):
			return nil, false
		}
	}
}

// ParseProfile is route.parse_profile: the flat `key: scalar` subset plus one nested `spend:` block.
func ParseProfile(text string) (map[string]any, error) {
	result := map[string]any{}
	inSpend := false
	for i, raw := range profileLines(text) {
		bad := &ProfileError{Line: i + 1, Text: raw}
		if pyStrip(raw) == "" || strings.HasPrefix(strings.TrimLeftFunc(raw, pySpace), "#") {
			continue
		}
		key, value, hasColon := strings.Cut(stripComment(raw), ":")
		key, value = pyStrip(key), pyStrip(value)
		if raw != strings.TrimLeftFunc(raw, pySpace) {
			if !inSpend || !hasColon || !slices.Contains(spendKeys, key) {
				return nil, bad
			}
			n, ok := pyDecimal(value)
			if !ok {
				return nil, bad
			}
			result[key] = n
			continue
		}
		inSpend = false
		switch {
		case !hasColon:
			return nil, bad
		case key == "spend" && value != "":
			return nil, bad
		case key == "spend":
			inSpend = true
		case !knownKey(key):
			return nil, bad
		case key == "sources" || key == "repo_map":
			v, ok := pyJSON(value)
			if !ok {
				return nil, bad
			}
			result[key] = v
		case strings.HasPrefix(value, "[") && strings.HasSuffix(value, "]") && len(value) >= 2:
			items := []string{}
			if inner := pyStrip(value[1 : len(value)-1]); inner != "" {
				for _, item := range strings.Split(inner, ",") {
					items = append(items, pyStrip(item))
				}
			}
			result[key] = items
		default:
			result[key] = value
		}
	}
	if result["assume"] == "" {
		delete(result, "assume")
	}
	if _, ok := result["assume"]; !ok {
		result["assume"] = "a"
	}
	return result, nil
}
