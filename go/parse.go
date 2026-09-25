package coxgo

import (
	"fmt"
	"maps"
	"slices"
	"strconv"
	"strings"
)

// groupDest and cmdDest are the two dests build_parser (agent_tools/cli.py and
// agent_tools/commands.py) fixes in code rather than reading from the table: the top
// parser's subparsers store the group name under "group", and a group with rows stores
// the row name under "cmd".
const (
	groupDest = "group"
	cmdDest   = "cmd"
)

// ParseError is what argparse would print as "<Prog>: error: <Message>".
type ParseError struct {
	Prog    string
	Message string
}

func (e *ParseError) Error() string { return e.Prog + ": error: " + e.Message }

// Parse returns the namespace argparse builds for argv (the words after the program name),
// without the `fn` default. Only the argparse subset the table uses is supported; -h/--help,
// "--", negative-number arguments and the top parser's own flags are not.
func Parse(t Table, argv []string) (map[string]any, error) {
	ns, extras, perr := parseLevel(t.Prog, rootCommand(t), argv)
	switch {
	case perr != nil:
		return nil, perr
	case len(extras) > 0:
		return nil, &ParseError{t.Prog, "unrecognized arguments: " + strings.Join(extras, " ")}
	default:
		return ns, nil
	}
}

// rootCommand and groupCommand fold the table into the recursive Command shape build_parser
// walks, so one function parses every level.
func rootCommand(t Table) Command {
	subs := make([]Command, len(t.Groups))
	for i, g := range t.Groups {
		subs[i] = groupCommand(g)
	}
	return Command{Name: t.Prog, Subcommands: subs, SubDest: groupDest}
}

func groupCommand(g Group) Command {
	if len(g.Commands) == 0 {
		return Command{Name: g.Name, Args: g.Args}
	}
	return Command{Name: g.Name, Args: g.Args, Subcommands: g.Commands, SubDest: cmdDest}
}

func isPositional(a Arg) bool { return len(a.Flags) == 0 || !strings.HasPrefix(a.Flags[0], "-") }

func optionLike(tok string) bool { return len(tok) > 1 && tok[0] == '-' }

// destOf is argparse's rule: the dest kwarg, else the first --long flag, else the first short flag.
func destOf(a Arg) string {
	if d, ok := a.Kwargs["dest"].(string); ok {
		return d
	}
	if isPositional(a) {
		return a.Flags[0]
	}
	i := slices.IndexFunc(a.Flags, func(f string) bool { return strings.HasPrefix(f, "--") })
	return strings.ReplaceAll(strings.TrimLeft(a.Flags[max(i, 0)], "-"), "-", "_")
}

// actionName is how an error names the argument: the flags joined by "/", or the metavar or dest.
func actionName(a Arg) string {
	if !isPositional(a) {
		return strings.Join(a.Flags, "/")
	}
	if m, ok := a.Kwargs["metavar"].(string); ok {
		return m
	}
	return destOf(a)
}

func defaultOf(a Arg) any {
	if d, ok := a.Kwargs["default"]; ok {
		return d
	}
	if a.Kwargs["action"] == "store_true" {
		return false
	}
	return nil
}

// seed is the namespace before any token is read: every dest at its default, the level's
// subcommand dest at nil, then the row's Defaults over the top, as set_defaults does.
func seed(c Command) map[string]any {
	ns := map[string]any{}
	for _, a := range c.Args {
		ns[destOf(a)] = defaultOf(a)
	}
	if len(c.Subcommands) > 0 && c.SubDest != "" {
		ns[c.SubDest] = nil
	}
	maps.Copy(ns, c.Defaults)
	return ns
}

func pyRepr(s string) string {
	quote := "'"
	if strings.Contains(s, "'") && !strings.Contains(s, `"`) {
		quote = `"`
	}
	s = strings.NewReplacer(`\`, `\\`, "\n", `\n`, "\t", `\t`, "\r", `\r`).Replace(s)
	if quote == "'" {
		s = strings.ReplaceAll(s, "'", `\'`)
	}
	return quote + s + quote
}

func joined(vs []any) string {
	parts := make([]string, len(vs))
	for i, v := range vs {
		parts[i] = fmt.Sprint(v)
	}
	return strings.Join(parts, ", ")
}

func invalidChoice(name string, value any, choices []any) string {
	shown := fmt.Sprint(value)
	if s, ok := value.(string); ok {
		shown = pyRepr(s)
	}
	return fmt.Sprintf("argument %s: invalid choice: %s (choose from %s)", name, shown, joined(choices))
}

// convert applies the argument's type, then its choices. A non-empty message is the error.
func convert(a Arg, raw string) (any, string) {
	name := actionName(a)
	var v any = raw
	switch a.Kwargs["type"] {
	case "int":
		n, err := strconv.ParseInt(raw, 10, 64)
		if err != nil {
			return nil, fmt.Sprintf("argument %s: invalid int value: %s", name, pyRepr(raw))
		}
		v = n
	case "float":
		f, err := strconv.ParseFloat(raw, 64)
		if err != nil {
			return nil, fmt.Sprintf("argument %s: invalid float value: %s", name, pyRepr(raw))
		}
		v = f
	}
	if choices, ok := a.Kwargs["choices"].([]any); ok {
		if !slices.ContainsFunc(choices, func(c any) bool { return fmt.Sprint(c) == fmt.Sprint(v) }) {
			return nil, invalidChoice(name, v, choices)
		}
	}
	return v, ""
}

type hit struct {
	arg    Arg
	val    string
	hasVal bool
}

// matchOption finds the flag a token names: exactly, with "=value", or by unique --long prefix.
// A nil hit with no message is an unknown option.
func matchOption(args []Arg, tok string) (*hit, string) {
	name, val, hasVal := tok, "", false
	if strings.HasPrefix(tok, "--") {
		name, val, hasVal = strings.Cut(tok, "=")
	}
	type candidate struct {
		arg  Arg
		flag string
	}
	var exact, prefixed []candidate
	for _, a := range args {
		if isPositional(a) {
			continue
		}
		for _, f := range a.Flags {
			if f == name || f == tok {
				exact = append(exact, candidate{a, f})
			} else if strings.HasPrefix(name, "--") && strings.HasPrefix(f, name) {
				prefixed = append(prefixed, candidate{a, f})
			}
		}
	}
	switch {
	case len(exact) > 0:
		return &hit{exact[0].arg, val, hasVal && exact[0].flag == name}, ""
	case len(prefixed) == 1:
		return &hit{prefixed[0].arg, val, hasVal}, ""
	case len(prefixed) > 1:
		flags := make([]string, len(prefixed))
		for i, c := range prefixed {
			flags[i] = c.flag
		}
		return nil, fmt.Sprintf("ambiguous option: %s could match %s", tok, strings.Join(flags, ", "))
	default:
		return nil, ""
	}
}

func appended(cur any, v any) any {
	if list, ok := cur.([]any); ok {
		return append(slices.Clone(list), v)
	}
	return []any{v}
}

func subName(c Command) string {
	if c.SubDest != "" {
		return c.SubDest
	}
	names := make([]string, len(c.Subcommands))
	for i, s := range c.Subcommands {
		names[i] = s.Name
	}
	return "{" + strings.Join(names, ",") + "}"
}

// parseLevel parses one parser's tokens: its own arguments, then the subcommand the first
// spare positional names, which takes every token after it. It returns the level's namespace
// (a child's dests laid over the parent's, as argparse copies them) and the tokens nobody claimed.
func parseLevel(prog string, c Command, tokens []string) (map[string]any, []string, *ParseError) {
	ns := seed(c)
	fail := func(msg string) (map[string]any, []string, *ParseError) {
		return nil, nil, &ParseError{prog, msg}
	}
	var pending []Arg
	for _, a := range c.Args {
		if isPositional(a) {
			pending = append(pending, a)
		}
	}
	seen := map[string]bool{}
	subbed := false
	var extras []string
	// argparse's scan is stateful; the index and the accumulators stay inside this function.
scan:
	for i := 0; i < len(tokens); i++ {
		tok := tokens[i]
		switch {
		case !optionLike(tok) && len(pending) > 0:
			v, msg := convert(pending[0], tok)
			if msg != "" {
				return fail(msg)
			}
			ns[destOf(pending[0])] = v
			pending = pending[1:]
		case !optionLike(tok) && len(c.Subcommands) > 0:
			j := slices.IndexFunc(c.Subcommands, func(s Command) bool { return s.Name == tok })
			if j < 0 {
				names := make([]any, len(c.Subcommands))
				for k, s := range c.Subcommands {
					names[k] = s.Name
				}
				return fail(invalidChoice(subName(c), tok, names))
			}
			if c.SubDest != "" {
				ns[c.SubDest] = tok
			}
			childNS, childExtras, err := parseLevel(prog+" "+tok, c.Subcommands[j], tokens[i+1:])
			if err != nil {
				return nil, nil, err
			}
			maps.Copy(ns, childNS)
			extras = append(extras, childExtras...)
			subbed = true
			break scan
		case !optionLike(tok):
			extras = append(extras, tok)
		default:
			h, msg := matchOption(c.Args, tok)
			if msg != "" {
				return fail(msg)
			}
			if h == nil {
				extras = append(extras, tok)
				continue
			}
			name, dest := actionName(h.arg), destOf(h.arg)
			seen[name] = true
			if h.arg.Kwargs["action"] == "store_true" {
				if h.hasVal {
					return fail(fmt.Sprintf("argument %s: ignored explicit argument %s", name, pyRepr(h.val)))
				}
				ns[dest] = true
				continue
			}
			raw := h.val
			switch {
			case h.hasVal:
			case i+1 < len(tokens) && !optionLike(tokens[i+1]):
				i++
				raw = tokens[i]
			default:
				return fail("argument " + name + ": expected one argument")
			}
			v, msg := convert(h.arg, raw)
			if msg != "" {
				return fail(msg)
			}
			if h.arg.Kwargs["action"] == "append" {
				v = appended(ns[dest], v)
			}
			ns[dest] = v
		}
	}
	var missing []string
	for _, a := range c.Args {
		switch {
		case isPositional(a):
			unfilled := slices.ContainsFunc(pending, func(p Arg) bool { return destOf(p) == destOf(a) })
			if unfilled && a.Kwargs["nargs"] != "?" {
				missing = append(missing, actionName(a))
			}
		case a.Kwargs["required"] == true && !seen[actionName(a)]:
			missing = append(missing, actionName(a))
		}
	}
	if len(c.Subcommands) > 0 && c.SubRequired && !subbed {
		missing = append(missing, subName(c))
	}
	if len(missing) > 0 {
		return fail("the following arguments are required: " + strings.Join(missing, ", "))
	}
	return ns, extras, nil
}
