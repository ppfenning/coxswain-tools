package coxgo

import (
	"bytes"
	"encoding/json"
	"math"
	"math/big"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

type profileCase struct {
	Text   string         `json:"text"`
	Result map[string]any `json:"result"`
	Error  *string        `json:"error"`
}

// num is a number compared by kind and value: an int keeps every digit, and NaN equals NaN.
type num struct{ Kind, Text string }

func floatNum(f float64) num {
	switch {
	case math.IsNaN(f):
		return num{"float", "nan"}
	case math.IsInf(f, 1):
		return num{"float", "inf"}
	case math.IsInf(f, -1):
		return num{"float", "-inf"}
	default:
		return num{"float", strconv.FormatFloat(f, 'g', -1, 64)}
	}
}

// canon puts a ParseProfile result and a cases-file result read with UseNumber in one form.
// Python writes a non-finite float as {"$float": "inf" | "-inf" | "nan"}.
func canon(v any) any {
	switch x := v.(type) {
	case float64:
		return floatNum(x)
	case json.Number:
		if strings.ContainsAny(string(x), ".eE") {
			f, _ := strconv.ParseFloat(string(x), 64)
			return floatNum(f)
		}
		n, _ := new(big.Int).SetString(string(x), 10)
		return num{"int", n.String()}
	case []string:
		out := make([]any, len(x))
		for i, s := range x {
			out[i] = s
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = canon(e)
		}
		return out
	case map[string]any:
		if s, ok := x["$float"].(string); ok && len(x) == 1 {
			return num{"float", s}
		}
		out := make(map[string]any, len(x))
		for k, e := range x {
			out[k] = canon(e)
		}
		return out
	default:
		return v
	}
}

func TestParseProfileMatchesTheCasesFile(t *testing.T) {
	raw, err := os.ReadFile("testdata/profile/cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []profileCase
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) == 0 {
		t.Fatal("cases file is empty")
	}
	for _, c := range cases {
		t.Run(c.Text, func(t *testing.T) {
			got, err := ParseProfile(c.Text)
			if c.Error != nil {
				if err == nil {
					t.Fatalf("no error, want %q", *c.Error)
				}
				if err.Error() != *c.Error {
					t.Fatalf("error %q, want %q", err.Error(), *c.Error)
				}
				return
			}
			if err != nil {
				t.Fatalf("error %q, want a result", err)
			}
			if g, w := canon(got), canon(c.Result); !reflect.DeepEqual(g, w) {
				t.Fatalf("got %v, want %v", g, w)
			}
		})
	}
}

func TestParseProfileKeepsBothCeilingsBesideAnOverflowingJSONNumber(t *testing.T) {
	c, err := parseProfile("sources: [1e999]\nspend:\n  window_ceiling_usd: 5\n  weekly_ceiling_usd: 50\n")
	if err != nil || c.Window == nil || *c.Window != 5 || c.Weekly == nil || *c.Weekly != 50 {
		t.Fatalf("parseProfile = %+v, %v; want both ceilings", c, err)
	}
}

func TestPyDecimalTakesPythonsDecimalForms(t *testing.T) {
	for s, want := range map[string]bool{
		"1_000": true, "1e3": true, ".5": true, "5.": true, "+1": true, "1e1_0": true,
		"1__0": false, "_1": false, "1_": false, "1_.5": false, "0x10": false, "0x1p3": false, "abc": false, "": false,
	} {
		if _, ok := pyDecimal(s); ok != want {
			t.Errorf("pyDecimal(%q) ok = %v, want %v", s, ok, want)
		}
	}
}
