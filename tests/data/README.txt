Tiny real archives for tests/test_layouts.py: what 7-Zip and WinRAR write, read by deltapatcher/layouts.py.

Made on 2026-11-25 on Windows 10 with 7-Zip 22.00 (x64) and RAR 7.12 x64 (WinRAR), from four files: a.bin (1500
random bytes), b.bin (1600), c.bin (1700) and notes.txt ("the notes of a game\n" 15 times, 300 bytes). In v2,
b.bin is 1600 other random bytes; the other three are the same. The files' times were set to 2026-11-01 12:00 first.

  7z-files-v1.7z, 7z-files-v2.7z   7z a -t7z -mx=5 -ms=off -mtm=off -mtc=off -mta=off <archive> a.bin b.bin c.bin notes.txt
                                   (file by file: one packed stream each; the header LZMA encoded, 7-Zip's default)
  7z-plain-header.7z               the same, v1, with -mhc=off: the header plain
  7z-solid.7z                      v1 without -ms=off: one solid block, one packed stream
  7z-encrypted-header.7z           v1 with -mhe=on -pfixture: the header encrypted (AES)
  rar5-files-v1.rar, -v2.rar       rar a -cfg- -idq -m3 -s- -tl -ep1 <archive> a.bin b.bin c.bin notes.txt
  rar5-solid.rar                   v1 with -s: a solid archive
  rar5-encrypted-headers.rar       v1 with -hpfixture: headers encrypted
  rar5-volumes.part1.rar, .part2   v1 with -m0 -v3k: a set of two volumes, b.bin continued from one to the other

RAR 7 no longer writes RAR4 archives; the RAR4 tests use the writer in tests/test_layouts.py. The password of the
two encrypted archives is "fixture": they are only ever read as far as their headers.

For tests/test_repack.py (repacking), made the same day with 7-Zip 22.00 and GNU gzip 1.10 (Git for Windows), from
a.bin (3000 random bytes), b.bin (3100), sub/c.txt ("hello" and a line end, 30 times), an empty file empty.txt and an
empty folder empty_dir:

  7z-solid-lzma2.7z   7z a -t7z -ms=on -mf=off -m0=LZMA2 <archive> *   (one solid folder; the header LZMA encoded)
  7z-solid-ppmd.7z    the same with -m0=PPMd: a coder repacking does not decode
  7z-solid-bcj2.7z    7z a -t7z -ms=on -mf=BCJ2 <archive> *            (BCJ2: four packed streams, not decoded)
  7z-solid-bcj-lzma.7z     7z a -t7z -ms=on -mf=BCJ -m0=LZMA <archive> *     (a branch filter over LZMA)
  7z-solid-arm-lzma.7z     the same with -mf=ARM
  7z-solid-sparc-lzma.7z   the same with -mf=SPARC
  7z-solid-deflate.7z      7z a -t7z -ms=on -mf=off -m0=Deflate <archive> *  (the test damages it byte by byte)
  gnu.tar.gz          tar -cf fx.tar a.bin b.bin sub empty.txt, then gzip -9 -n -c fx.tar
