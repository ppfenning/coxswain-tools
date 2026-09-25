// cox-go is the phase 1 spike of the Go CLI; the Python `cox` stays the shipped CLI.
package main

import (
	"fmt"
	"os"

	coxgo "github.com/ppfenning/coxswain-tools/go"
)

func main() {
	table, err := coxgo.Load()
	if err != nil {
		fmt.Fprintln(os.Stderr, "cox-go:", err)
		os.Exit(1)
	}
	fmt.Printf("cox-go: %d groups; nothing is ported yet\n", len(table.Groups))
}
