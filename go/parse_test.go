package coxgo

import (
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"
)

type parseCase struct {
	Argv   []string       `json:"argv"`
	Values map[string]any `json:"values"`
	Error  *ParseError    `json:"error"`
}

// viaJSON puts a value through encoding/json, so 3 and 3.0 compare the way the cases file does.
func viaJSON(t *testing.T, v any) any {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

// sameMessage is an exact match, except for an unknown group. Python lists the groups in the
// order cli.py registers them, with the hand-added dev and release; the table has neither that
// order nor those two, so only the text before the choices can be compared.
func sameMessage(want, got string) bool {
	const unknownGroup = "argument group: invalid choice: "
	if strings.HasPrefix(want, unknownGroup) {
		cut := func(s string) string { head, _, _ := strings.Cut(s, " (choose from "); return head }
		return strings.HasPrefix(got, unknownGroup) && cut(want) == cut(got)
	}
	return want == got
}

func TestParseMatchesTheCasesFile(t *testing.T) {
	raw, err := os.ReadFile("testdata/parse/cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []parseCase
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	table, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		t.Run(strings.Join(c.Argv, " "), func(t *testing.T) {
			got, err := Parse(table, c.Argv)
			if c.Error != nil {
				var pe *ParseError
				if !errors.As(err, &pe) {
					t.Fatalf("want error %+v, got values %v and err %v", *c.Error, got, err)
				}
				if pe.Prog != c.Error.Prog || !sameMessage(c.Error.Message, pe.Message) {
					t.Fatalf("want %+v, got %+v", *c.Error, *pe)
				}
				return
			}
			if err != nil {
				t.Fatalf("want values %v, got error %v", c.Values, err)
			}
			if want, have := viaJSON(t, c.Values), viaJSON(t, got); !reflect.DeepEqual(want, have) {
				t.Fatalf("want %v, got %v", want, have)
			}
		})
	}
}
