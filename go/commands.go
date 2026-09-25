// Package coxgo is the Go CLI's shared core. The command table is the Python CLI's own,
// exported to commands.json by `python -m agent_tools.command_table_json`, so the two cannot drift.
package coxgo

import (
	_ "embed"
	"encoding/json"
)

//go:embed commands.json
var commandsJSON []byte

type Arg struct {
	Flags  []string       `json:"flags"`
	Kwargs map[string]any `json:"kwargs"`
}

type Command struct {
	Name        string         `json:"name"`
	Summary     string         `json:"summary"`
	Slash       bool           `json:"slash"`
	Examples    []string       `json:"examples"`
	Args        []Arg          `json:"args"`
	Subcommands []Command      `json:"subcommands"`
	SubDest     string         `json:"sub_dest"`
	SubRequired bool           `json:"sub_required"`
	Defaults    map[string]any `json:"defaults"`
}

type Group struct {
	Name        string    `json:"name"`
	Help        string    `json:"help"`
	Description string    `json:"description"`
	Epilog      string    `json:"epilog"`
	Args        []Arg     `json:"args"`
	Commands    []Command `json:"commands"`
}

type Table struct {
	Prog        string  `json:"prog"`
	Description string  `json:"description"`
	Groups      []Group `json:"groups"`
}

// Load parses the embedded command table.
func Load() (Table, error) {
	var t Table
	err := json.Unmarshal(commandsJSON, &t)
	return t, err
}
