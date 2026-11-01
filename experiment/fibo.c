#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

/* A fixed amount of recursive work, never a wall-time-controlled loop. */
static volatile uint64_t sink;
__attribute__((noinline)) static uint64_t fib(unsigned n) {
    if (n < 2) return n;
    return fib(n - 1) + fib(n - 2);
}
static double seconds(clockid_t id) {
    struct timespec t;
    if (clock_gettime(id, &t)) { perror("clock_gettime"); exit(2); }
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}
static uint64_t number(const char *s, uint64_t lo, uint64_t hi) {
    char *end;
    errno = 0;
    unsigned long long v = strtoull(s, &end, 10);
    if (errno || !*s || *end || *s == '-' || v < lo || v > hi) {
        fprintf(stderr, "invalid numeric argument: %s\n", s); exit(2);
    }
    return (uint64_t)v;
}
int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s N REPEATS (2<=N<=48)\n", argv[0]); return 2;
    }
    unsigned n = (unsigned)number(argv[1], 2, 48);
    uint64_t repeats = number(argv[2], 1, 1000000000ULL);
    double wall0 = seconds(CLOCK_MONOTONIC);
    double cpu0 = seconds(CLOCK_PROCESS_CPUTIME_ID);
    for (uint64_t i = 0; i < repeats; ++i) {
        /* The volatile argument prevents lifting fib() outside this loop. */
        volatile unsigned input = n;
        sink += fib(input);
    }
    double cpu = seconds(CLOCK_PROCESS_CPUTIME_ID) - cpu0;
    double wall = seconds(CLOCK_MONOTONIC) - wall0;
    printf("{\"n\":%u,\"repeats\":%" PRIu64 ",\"checksum\":%" PRIu64
           ",\"cpu_seconds\":%.9f,\"wall_seconds\":%.9f}\n",
           n, repeats, sink, cpu, wall);
    return 0;
}
