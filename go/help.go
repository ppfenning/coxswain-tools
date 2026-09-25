package coxgo

// Help rendering: a port of the parts of CPython 3.12's argparse.HelpFormatter and
// RawDescriptionHelpFormatter that the cox parsers use. Everything here is pure: it takes the
// command table and a width and returns text. Lengths are in runes, as Python counts them.

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

const (
	indentStep      = 2
	maxHelpPosition = 24
	minHelpWidth    = 11
	usagePrefix     = "usage: "
	helpFlagText    = "show this help message and exit"
)

// action is one argparse Action reduced to what help formatting reads. nargs is "" for None,
// "0" for a flag, "?", "*", "+", a digit string, or "parser" for the subparsers action.
type action struct {
	flags    []string
	dest     string
	metavar  string
	choices  []string
	nargs    string
	required bool
	help     string
	subs     []action
}

// parser is one argparse parser: positionals and optionals each in the order they were added.
type parser struct {
	prog        string
	description string
	epilog      string
	positionals []action
	optionals   []action
}

// GroupHelp renders `cox <group> --help`. width is the terminal width minus 2.
func GroupHelp(t Table, group string, width int) (string, error) {
	g, ok := findGroup(t, group)
	if !ok {
		return "", fmt.Errorf("unknown group %q", group)
	}
	p := parser{prog: t.Prog + " " + g.Name, description: g.Description, epilog: g.Epilog}
	p.positionals, p.optionals = splitArgs(g.Args)
	if len(g.Commands) > 0 {
		p.positionals = append(p.positionals, subparsersAction(g.Commands))
	}
	return formatHelp(p, width), nil
}

// CommandHelp renders `cox <group> <command> --help`. width is the terminal width minus 2.
func CommandHelp(t Table, group, command string, width int) (string, error) {
	g, ok := findGroup(t, group)
	if !ok {
		return "", fmt.Errorf("unknown group %q", group)
	}
	for _, c := range g.Commands {
		if c.Name == command {
			p := parser{prog: t.Prog + " " + g.Name + " " + c.Name}
			p.positionals, p.optionals = splitArgs(c.Args)
			return formatHelp(p, width), nil
		}
	}
	return "", fmt.Errorf("unknown command %q in group %q", command, group)
}

func findGroup(t Table, name string) (Group, bool) {
	for _, g := range t.Groups {
		if g.Name == name {
			return g, true
		}
	}
	return Group{}, false
}

// ---- table to actions ----

func splitArgs(args []Arg) (positionals, optionals []action) {
	optionals = []action{{flags: []string{"-h", "--help"}, dest: "help", nargs: "0", help: helpFlagText}}
	for _, a := range args {
		act := argAction(a)
		if len(act.flags) == 0 {
			positionals = append(positionals, act)
		} else {
			optionals = append(optionals, act)
		}
	}
	return positionals, optionals
}

func argAction(a Arg) action {
	k := a.Kwargs
	act := action{help: kwString(k, "help"), metavar: kwString(k, "metavar")}
	if len(a.Flags) > 0 && strings.HasPrefix(a.Flags[0], "-") {
		act.flags = a.Flags
		act.dest = deriveDest(a.Flags)
	} else if len(a.Flags) > 0 {
		act.dest = a.Flags[0]
	}
	if d := kwString(k, "dest"); d != "" {
		act.dest = d
	}
	if req, _ := k["required"].(bool); req {
		act.required = true
	}
	if cs, ok := k["choices"].([]any); ok {
		for _, c := range cs {
			act.choices = append(act.choices, fmt.Sprint(c))
		}
	}
	switch n := k["nargs"].(type) {
	case string:
		act.nargs = n
	case float64:
		act.nargs = strconv.Itoa(int(n))
	}
	if kwString(k, "action") == "store_true" || kwString(k, "action") == "store_false" {
		act.nargs = "0"
	}
	return act
}

func kwString(k map[string]any, key string) string {
	s, _ := k[key].(string)
	return s
}

// deriveDest is argparse's rule: the first long option string, else the first, minus its dashes.
func deriveDest(flags []string) string {
	first := flags[0]
	for _, f := range flags {
		if strings.HasPrefix(f, "--") {
			first = f
			break
		}
	}
	return strings.ReplaceAll(strings.TrimLeft(first, "-"), "-", "_")
}

func subparsersAction(cmds []Command) action {
	a := action{nargs: "parser"}
	for _, c := range cmds {
		a.choices = append(a.choices, c.Name)
		a.subs = append(a.subs, action{dest: c.Name, metavar: c.Name, help: c.Summary})
	}
	return a
}

// ---- metavars and invocations ----

func metavarOf(a action, def string) string {
	switch {
	case a.metavar != "":
		return a.metavar
	case a.choices != nil:
		return "{" + strings.Join(a.choices, ",") + "}"
	default:
		return def
	}
}

func formatArgs(a action, def string) string {
	mv := metavarOf(a, def)
	switch a.nargs {
	case "":
		return mv
	case "?":
		return "[" + mv + "]"
	case "*":
		return "[" + mv + " ...]"
	case "+":
		return mv + " [" + mv + " ...]"
	case "parser":
		return mv + " ..."
	}
	n, _ := strconv.Atoi(a.nargs)
	return strings.TrimSuffix(strings.Repeat(mv+" ", n), " ")
}

func invocation(a action) string {
	if len(a.flags) == 0 {
		return metavarOf(a, a.dest)
	}
	if a.nargs == "0" {
		return strings.Join(a.flags, ", ")
	}
	args := formatArgs(a, strings.ToUpper(a.dest))
	parts := make([]string, len(a.flags))
	for i, f := range a.flags {
		parts[i] = f + " " + args
	}
	return strings.Join(parts, ", ")
}

// ---- usage ----

var (
	openSpace  = regexp.MustCompile(`([\[(]) `)
	spaceClose = regexp.MustCompile(` ([\])])`)
	emptyPair  = regexp.MustCompile(`[\[(] *[\])]`)
)

func actionsUsage(actions []action) string {
	parts := make([]string, 0, len(actions))
	for _, a := range actions {
		switch {
		case len(a.flags) == 0:
			parts = append(parts, formatArgs(a, a.dest))
		default:
			part := a.flags[0]
			if a.nargs != "0" {
				part += " " + formatArgs(a, strings.ToUpper(a.dest))
			}
			if !a.required {
				part = "[" + part + "]"
			}
			parts = append(parts, part)
		}
	}
	text := strings.Join(parts, " ")
	text = openSpace.ReplaceAllString(text, "$1")
	text = spaceClose.ReplaceAllString(text, "$1")
	text = emptyPair.ReplaceAllString(text, "")
	return strings.TrimSpace(text)
}

// usageParts is argparse's part_regexp `\(.*?\)+(?=\s|$)|\[.*?\]+(?=\s|$)|\S+` without lookahead:
// a bracketed run ends at the first closing bracket run that is followed by whitespace or the end.
func usageParts(s string) []string {
	r := []rune(s)
	var parts []string
	for i := 0; i < len(r); {
		if unicode.IsSpace(r[i]) {
			i++
			continue
		}
		end := i
		if r[i] == '(' || r[i] == '[' {
			end = bracketEnd(r, i)
		}
		if end == i {
			for end < len(r) && !unicode.IsSpace(r[end]) {
				end++
			}
		}
		parts = append(parts, string(r[i:end]))
		i = end
	}
	return parts
}

// bracketEnd returns the end of the bracketed part starting at i, or i when there is none.
func bracketEnd(r []rune, i int) int {
	closer := ']'
	if r[i] == '(' {
		closer = ')'
	}
	for j := i + 1; j < len(r); j++ {
		if r[j] != closer {
			continue
		}
		k := j
		for k < len(r) && r[k] == closer {
			k++
		}
		if k == len(r) || unicode.IsSpace(r[k]) {
			return k
		}
		j = k - 1
	}
	return i
}

func formatUsage(prog string, optionals, positionals []action, width int) string {
	all := append(append([]action{}, optionals...), positionals...)
	usage := strings.TrimSpace(prog + " " + actionsUsage(all))
	if runeLen(usagePrefix)+runeLen(usage) > width {
		optParts := usageParts(actionsUsage(optionals))
		posParts := usageParts(actionsUsage(positionals))
		usage = strings.Join(wrapUsage(prog, optParts, posParts, width), "\n")
	}
	return usagePrefix + usage + "\n\n"
}

func wrapUsage(prog string, optParts, posParts []string, width int) []string {
	getLines := func(parts []string, indent string, withPrefix bool) []string {
		var lines, line []string
		indentLen := runeLen(indent)
		lineLen := indentLen - 1
		if withPrefix {
			lineLen = runeLen(usagePrefix) - 1
		}
		for _, part := range parts {
			if lineLen+1+runeLen(part) > width && len(line) > 0 {
				lines = append(lines, indent+strings.Join(line, " "))
				line = nil
				lineLen = indentLen - 1
			}
			line = append(line, part)
			lineLen += runeLen(part) + 1
		}
		if len(line) > 0 {
			lines = append(lines, indent+strings.Join(line, " "))
		}
		if withPrefix {
			lines[0] = string([]rune(lines[0])[indentLen:])
		}
		return lines
	}
	// len(prefix)+len(prog) <= 0.75*width, in integers
	if 4*(runeLen(usagePrefix)+runeLen(prog)) <= 3*width {
		indent := spaces(runeLen(usagePrefix) + runeLen(prog) + 1)
		switch {
		case len(optParts) > 0:
			lines := getLines(append([]string{prog}, optParts...), indent, true)
			return append(lines, getLines(posParts, indent, false)...)
		case len(posParts) > 0:
			return getLines(append([]string{prog}, posParts...), indent, true)
		default:
			return []string{prog}
		}
	}
	indent := spaces(runeLen(usagePrefix))
	lines := getLines(append(append([]string{}, optParts...), posParts...), indent, false)
	if len(lines) > 1 {
		lines = append(getLines(optParts, indent, false), getLines(posParts, indent, false)...)
	}
	return append([]string{prog}, lines...)
}

// ---- action sections ----

func maxInvocation(actions []action) int {
	m := 0
	for _, a := range actions {
		m = max(m, runeLen(invocation(a))+indentStep)
		for _, s := range a.subs {
			m = max(m, runeLen(invocation(s))+2*indentStep)
		}
	}
	return m
}

func formatAction(a action, prog string, indent, helpPos, width int) string {
	helpWidth := max(width-helpPos, minHelpWidth)
	actionWidth := helpPos - indent - 2
	header := invocation(a)
	var b strings.Builder
	indentFirst := 0
	switch {
	case a.help == "":
		b.WriteString(spaces(indent) + header + "\n")
	case runeLen(header) <= actionWidth:
		b.WriteString(spaces(indent) + padRight(header, actionWidth) + "  ")
	default:
		b.WriteString(spaces(indent) + header + "\n")
		indentFirst = helpPos
	}
	if strings.TrimSpace(a.help) != "" {
		if lines := splitLines(expandHelp(a.help, prog), helpWidth); len(lines) > 0 {
			b.WriteString(spaces(indentFirst) + lines[0] + "\n")
			for _, l := range lines[1:] {
				b.WriteString(spaces(helpPos) + l + "\n")
			}
		}
	}
	for _, s := range a.subs {
		b.WriteString(formatAction(s, prog, indent+indentStep, helpPos, width))
	}
	return b.String()
}

// expandHelp does the two substitutions help text may carry: %(prog)s and %%.
func expandHelp(help, prog string) string {
	return strings.NewReplacer("%(prog)s", prog, "%%", "%").Replace(help)
}

// expandText is _format_text's rule: a description or epilog is %-formatted only when it
// contains "%(prog)", so a lone "%%" elsewhere stays as written.
func expandText(text, prog string) string {
	if !strings.Contains(text, "%(prog)") {
		return text
	}
	return expandHelp(text, prog)
}

func formatSection(heading string, actions []action, prog string, helpPos, width int) string {
	var items strings.Builder
	for _, a := range actions {
		items.WriteString(formatAction(a, prog, indentStep, helpPos, width))
	}
	if items.Len() == 0 {
		return ""
	}
	return "\n" + heading + ":\n" + items.String() + "\n"
}

var longBreak = regexp.MustCompile(`\n\n\n+`)

func formatHelp(p parser, width int) string {
	maxPos := min(maxHelpPosition, max(width-20, indentStep*2))
	helpPos := min(maxInvocation(append(append([]action{}, p.positionals...), p.optionals...))+2, maxPos)
	var b strings.Builder
	b.WriteString(formatUsage(p.prog, p.optionals, p.positionals, width))
	if p.description != "" {
		b.WriteString(fillRaw(expandText(p.description, p.prog), "") + "\n\n")
	}
	b.WriteString(formatSection("positional arguments", p.positionals, p.prog, helpPos, width))
	b.WriteString(formatSection("options", p.optionals, p.prog, helpPos, width))
	if p.epilog != "" {
		b.WriteString(fillRaw(expandText(p.epilog, p.prog), "") + "\n\n")
	}
	return strings.Trim(longBreak.ReplaceAllString(b.String(), "\n\n"), "\n") + "\n"
}

// ---- text ----

// fillRaw is RawDescriptionHelpFormatter._fill_text: indent every line, keep the rest.
func fillRaw(text, indent string) string {
	var b strings.Builder
	for _, line := range strings.SplitAfter(text, "\n") {
		if line != "" {
			b.WriteString(indent + line)
		}
	}
	return b.String()
}

var whitespaceRun = regexp.MustCompile(`[ \t\n\v\f\r]+`)

// splitLines is HelpFormatter._split_lines: collapse whitespace, then textwrap.wrap.
func splitLines(text string, width int) []string {
	text = strings.TrimSpace(whitespaceRun.ReplaceAllString(text, " "))
	return wrapChunks(wordChunks(text), width)
}

func isWord(r rune) bool {
	return r == '_' || unicode.IsLetter(r) || unicode.IsNumber(r)
}

func isLetter(r rune) bool { return isWord(r) && !unicode.IsDigit(r) }

func isWordPunct(r rune) bool { return isWord(r) || strings.ContainsRune(`!"'&.,?`, r) }

func isBlank(r rune) bool { return strings.ContainsRune(" \t\n\v\f\r", r) }

// wordChunks is textwrap's wordsep_re split: whitespace runs, em-dash runs between words, and
// word pieces that end after a hyphen inside a hyphenated word, at a word end, or before an em-dash.
func wordChunks(text string) []string {
	r := []rune(text)
	at := func(i int) rune {
		if i < 0 || i >= len(r) {
			return 0
		}
		return r[i]
	}
	hyphenRun := func(i int) int {
		for i < len(r) && r[i] == '-' {
			i++
		}
		return i
	}
	var chunks []string
	for i := 0; i < len(r); {
		var end int
		switch {
		case isBlank(r[i]):
			for end = i; end < len(r) && isBlank(r[end]); end++ {
			}
		case r[i] == '-' && i > 0 && isWordPunct(r[i-1]) && hyphenRun(i)-i >= 2 && isWord(at(hyphenRun(i))):
			end = hyphenRun(i)
		default:
			end = wordEnd(r, i)
		}
		chunks = append(chunks, string(r[i:end]))
		i = end
	}
	return chunks
}

// wordEnd finds the earliest end of the word piece starting at i, per wordsep_re's third branch.
func wordEnd(r []rune, i int) int {
	at := func(j int) rune {
		if j < 0 || j >= len(r) {
			return 0
		}
		return r[j]
	}
	for e := i + 1; ; e++ {
		if e >= len(r) || isBlank(r[e]) {
			return e
		}
		if r[e-1] == '-' && e-1 > i {
			behind := isLetter(at(e-3)) && isLetter(at(e-2)) ||
				isLetter(at(e-4)) && at(e-3) == '-' && isLetter(at(e-2))
			ahead := isLetter(at(e)) && (isLetter(at(e+1)) || at(e+1) == '-' && isLetter(at(e+2)))
			if behind && ahead {
				return e
			}
		}
		if isWordPunct(r[e-1]) && r[e] == '-' {
			k := e
			for k < len(r) && r[k] == '-' {
				k++
			}
			if k-e >= 2 && isWord(at(k)) {
				return e
			}
		}
	}
}

// wrapChunks is TextWrapper._wrap_chunks with the defaults: drop_whitespace, break_long_words
// and break_on_hyphens on, no indent.
func wrapChunks(chunks []string, width int) []string {
	stack := make([]string, len(chunks))
	for i, c := range chunks {
		stack[len(chunks)-1-i] = c
	}
	var lines []string
	for len(stack) > 0 {
		var cur []string
		curLen := 0
		if len(lines) > 0 && strings.TrimSpace(stack[len(stack)-1]) == "" {
			stack = stack[:len(stack)-1]
		}
		for len(stack) > 0 {
			l := runeLen(stack[len(stack)-1])
			if curLen+l > width {
				break
			}
			cur = append(cur, stack[len(stack)-1])
			stack = stack[:len(stack)-1]
			curLen += l
		}
		if len(stack) > 0 && runeLen(stack[len(stack)-1]) > width {
			cur = breakLongWord(stack, cur, curLen, width)
		}
		if len(cur) > 0 && strings.TrimSpace(cur[len(cur)-1]) == "" {
			cur = cur[:len(cur)-1]
		}
		if len(cur) > 0 {
			lines = append(lines, strings.Join(cur, ""))
		}
	}
	return lines
}

// breakLongWord is TextWrapper._handle_long_word: it moves the head of the top chunk onto cur,
// preferring to break after the last hyphen that has a non-hyphen before it.
func breakLongWord(stack, cur []string, curLen, width int) []string {
	spaceLeft := 1
	if width >= 1 {
		spaceLeft = width - curLen
	}
	top := len(stack) - 1
	chunk := []rune(stack[top])
	end := spaceLeft
	if len(chunk) > spaceLeft {
		h := -1
		for i := 0; i < spaceLeft; i++ {
			if chunk[i] == '-' {
				h = i
			}
		}
		if h > 0 && strings.Trim(string(chunk[:h]), "-") != "" {
			end = h + 1
		}
	}
	stack[top] = string(chunk[end:])
	return append(cur, string(chunk[:end]))
}

func runeLen(s string) int { return utf8.RuneCountInString(s) }

func spaces(n int) string { return strings.Repeat(" ", n) }

func padRight(s string, width int) string { return s + spaces(max(width-runeLen(s), 0)) }
