package coxgo

import "testing"

func TestTheEmbeddedTableLoadsWithNamedGroupsAndCommands(t *testing.T) {
	table, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if table.Prog != "cox" || len(table.Groups) == 0 {
		t.Fatalf("prog %q with %d groups", table.Prog, len(table.Groups))
	}
	for _, g := range table.Groups {
		if g.Name == "" {
			t.Fatal("a group has no name")
		}
		for _, c := range g.Commands {
			if c.Name == "" {
				t.Fatalf("a command in %s has no name", g.Name)
			}
		}
	}
}
