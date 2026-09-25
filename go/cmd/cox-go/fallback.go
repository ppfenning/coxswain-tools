package main

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
)

// pythonTarget is COX_PY unless it resolves to self, else the first cox on PATH that does not
// resolve to self, with one lookPath per PATH entry. An empty self refuses PATH, which could loop.
func pythonTarget(getenv func(string) string, lookPath func(string) (string, error), self string) (string, error) {
	if py := getenv("COX_PY"); py != "" {
		if resolved, err := filepath.EvalSymlinks(py); err == nil && resolved == self {
			return "", errors.New("COX_PY is cox-go itself; set COX_PY to the Python cox")
		}
		return py, nil
	}
	if self == "" {
		return "", errors.New("cox-go cannot resolve its own path to skip it on PATH; set COX_PY")
	}
	for _, dir := range filepath.SplitList(getenv("PATH")) {
		if dir == "" {
			continue
		}
		hit, err := lookPath(dir + string(filepath.Separator) + "cox")
		if err != nil {
			continue
		}
		if resolved, err := filepath.EvalSymlinks(hit); err == nil && resolved != self {
			return hit, nil
		}
	}
	return "", errors.New("no Python cox found on PATH; set COX_PY")
}

// selfPath is the running binary's resolved path, or "" when it cannot be found.
func selfPath() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	resolved, err := filepath.EvalSymlinks(exe)
	if err != nil {
		return exe
	}
	return resolved
}

// notPorted is the stderr line for an argv that has no Python target.
func notPorted(args []string) string {
	return "cox-go: " + strings.Join(args, " ") + " is not ported and no Python cox was found; set COX_PY\n"
}
