//go:build windows

package logger

import (
	"io"
	"os"

	"golang.org/x/sys/windows"
)

const enableVirtualTerminalProcessing = 0x0004

func detectColorSupport(w io.Writer) bool {
	f, ok := w.(*os.File)
	if !ok {
		return false
	}

	handle := windows.Handle(f.Fd())
	var mode uint32
	if err := windows.GetConsoleMode(handle, &mode); err != nil {
		return false
	}

	if mode&enableVirtualTerminalProcessing != 0 {
		return true
	}

	return windows.SetConsoleMode(handle, mode|enableVirtualTerminalProcessing) == nil
}
