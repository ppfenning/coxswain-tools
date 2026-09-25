package main

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// fakeCox writes an executable named cox into a new directory. It returns the directory, the file,
// and the file's resolved path, which is what pythonTarget compares against self.
func fakeCox(t *testing.T) (dir, file, resolved string) {
	t.Helper()
	dir = t.TempDir()
	file = filepath.Join(dir, "cox")
	if err := os.WriteFile(file, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	resolved, err := filepath.EvalSymlinks(file)
	if err != nil {
		t.Fatal(err)
	}
	return dir, file, resolved
}

func envOf(m map[string]string) func(string) string { return func(k string) string { return m[k] } }

func found(path string) (string, error) {
	if _, err := os.Stat(path); err != nil {
		return "", err
	}
	return path, nil
}

func TestPythonTargetPrefersCoxPyOverPath(t *testing.T) {
	dir, _, _ := fakeCox(t)
	got, err := pythonTarget(envOf(map[string]string{"COX_PY": "/opt/py/cox", "PATH": dir}), found, "/self")
	if got != "/opt/py/cox" || err != nil {
		t.Errorf("got %q, %v", got, err)
	}
}

func TestPythonTargetSkipsAPathHitThatIsSelfForTheNextOne(t *testing.T) {
	selfDir, _, self := fakeCox(t)
	otherDir, other, _ := fakeCox(t)
	got, err := pythonTarget(envOf(map[string]string{"PATH": selfDir + string(os.PathListSeparator) + otherDir}), found, self)
	if got != other || err != nil {
		t.Errorf("got %q, %v, want %q", got, err, other)
	}
}

func TestPythonTargetRefusesACoxPyThatIsSelfAndPathWhenSelfIsUnknown(t *testing.T) {
	dir, file, self := fakeCox(t)
	if got, err := pythonTarget(envOf(map[string]string{"COX_PY": file}), found, self); got != "" || err == nil {
		t.Errorf("COX_PY is self: got %q, %v", got, err)
	}
	if got, err := pythonTarget(envOf(map[string]string{"PATH": dir}), found, ""); got != "" || err == nil || !strings.Contains(err.Error(), "COX_PY") {
		t.Errorf("empty self: got %q, %v", got, err)
	}
}

func TestPythonTargetWithNeitherIsAnErrorNamingCoxPy(t *testing.T) {
	for _, path := range []string{"", t.TempDir()} {
		got, err := pythonTarget(envOf(map[string]string{"PATH": path}), found, "/self")
		if got != "" || err == nil || !strings.Contains(err.Error(), "COX_PY") {
			t.Errorf("PATH %q: got %q, %v", path, got, err)
		}
	}
}

// runBinary runs the built cox-go with extra environment and returns stdout, stderr and the exit code.
func runBinary(t *testing.T, bin string, env []string, args ...string) (stdout, stderr string, code int) {
	t.Helper()
	var out, errOut strings.Builder
	cmd := exec.Command(bin, args...)
	cmd.Env = append(os.Environ(), env...)
	cmd.Stdout, cmd.Stderr = &out, &errOut
	err := cmd.Run()
	var exit *exec.ExitError
	switch {
	case err == nil:
		return out.String(), errOut.String(), 0
	case errors.As(err, &exit):
		return out.String(), errOut.String(), exit.ExitCode()
	default:
		t.Fatalf("run %q: %v", args, err)
		return "", "", -1
	}
}

func TestBuiltBinaryHandsAnUnportedArgvToCoxPyWithItsExitCode(t *testing.T) {
	if _, err := exec.LookPath("go"); err != nil {
		t.Skip("go is not on PATH")
	}
	tmp := t.TempDir()
	bin := filepath.Join(tmp, "cox-go")
	if out, err := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("go build: %v\n%s", err, out)
	}
	script := filepath.Join(tmp, "fake-cox.sh")
	if err := os.WriteFile(script, []byte("#!/bin/sh\necho \"$0 $@\"\nexit 7\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"runs", "series", "--json"}, {"usage", "assess", "--json", "--runs-dir", "runs"}} {
		stdout, stderr, code := runBinary(t, bin, []string{"COX_PY=" + script}, args...)
		if want := script + " " + strings.Join(args, " ") + "\n"; stdout != want || stderr != "" || code != 7 {
			t.Errorf("%q: got stdout %q, stderr %q, code %d, want %q and 7", args, stdout, stderr, code, want)
		}
	}
	stdout, stderr, code := runBinary(t, bin, []string{"COX_PY=", "PATH=" + t.TempDir()}, "runs", "series", "--json")
	want := "cox-go: runs series --json is not ported and no Python cox was found; set COX_PY\n"
	if stdout != "" || stderr != want || code != 2 {
		t.Errorf("no target: got stdout %q, stderr %q, code %d", stdout, stderr, code)
	}
}
