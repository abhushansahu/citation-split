// setvol "<device name>" [0-100]   sets (or prints) an output device's volume without changing the default output
#include <CoreAudio/CoreAudio.h>
#include <AudioToolbox/AudioServices.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static AudioDeviceID find_device(const char *want) {
    AudioObjectPropertyAddress pa = { kAudioHardwarePropertyDevices, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain };
    UInt32 sz = 0; AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &pa, 0, NULL, &sz);
    AudioDeviceID *ids = malloc(sz); AudioObjectGetPropertyData(kAudioObjectSystemObject, &pa, 0, NULL, &sz, ids);
    AudioDeviceID found = kAudioObjectUnknown;
    for (UInt32 i = 0; i < sz / sizeof(AudioDeviceID); i++) {
        CFStringRef name = NULL; UInt32 nsz = sizeof(name);
        AudioObjectPropertyAddress na = { kAudioDevicePropertyDeviceNameCFString, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain };
        if (AudioObjectGetPropertyData(ids[i], &na, 0, NULL, &nsz, &name) != noErr || !name) continue;
        char buf[256]; CFStringGetCString(name, buf, sizeof buf, kCFStringEncodingUTF8); CFRelease(name);
        AudioObjectPropertyAddress oa = { kAudioDevicePropertyStreams, kAudioObjectPropertyScopeOutput, kAudioObjectPropertyElementMain };
        UInt32 osz = 0; AudioObjectGetPropertyDataSize(ids[i], &oa, 0, NULL, &osz);
        if (osz > 0 && strcmp(buf, want) == 0) { found = ids[i]; break; }
    }
    free(ids); return found;
}
int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: setvol \"device name\" [0-100]\n"); return 2; }
    AudioDeviceID dev = find_device(argv[1]);
    if (dev == kAudioObjectUnknown) { fprintf(stderr, "output device not found: %s\n", argv[1]); return 1; }
    AudioObjectPropertyAddress va = { kAudioHardwareServiceDeviceProperty_VirtualMainVolume, kAudioObjectPropertyScopeOutput, kAudioObjectPropertyElementMain };
    Float32 v; UInt32 vsz = sizeof v;
    if (argc >= 3) {
        v = atof(argv[2]) / 100.0f; if (v < 0) v = 0; if (v > 1) v = 1;
        OSStatus st = AudioObjectSetPropertyData(dev, &va, 0, NULL, sizeof v, &v);
        if (st != noErr) { fprintf(stderr, "cannot set volume on %s (err %d)\n", argv[1], (int)st); return 1; }
    }
    if (AudioObjectGetPropertyData(dev, &va, 0, NULL, &vsz, &v) == noErr) printf("%s %.0f%%\n", argv[1], v * 100);
    return 0;
}
