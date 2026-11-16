#include "itch/feed/moldudp/live.hpp"
#include "itch/book/book.hpp"
#include "itch/common/binary.hpp"
#include "itch/common/digest.hpp"
#include "itch/feed/itch/decoder.hpp"
#include "itch/feed/moldudp/moldudp.hpp"
#include "itch/output/csv.hpp"
#include <algorithm>
#include <arpa/inet.h>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <iostream>
#include <map>
#include <optional>
#include <poll.h>
#include <stdexcept>
#include <sys/socket.h>
#include <unistd.h>
#include <vector>

namespace itch {
namespace {
using Clock = std::chrono::steady_clock;
using Ms = std::chrono::milliseconds;

/** Close the descriptor on all exit paths, including exceptions. */
struct Socket {
    int fd = socket(AF_INET, SOCK_DGRAM, 0);

    Socket() {
        if (fd < 0) {
            throw std::runtime_error("cannot create UDP socket");
        }
    }

    ~Socket() {
        close(fd);
    }
};

/** Parse an explicit unicast endpoint; no DNS, wildcard bind or multicast membership. */
sockaddr_in address(const std::string& text) {
    const auto colon = text.find(':');
    if (colon == std::string::npos) {
        throw std::runtime_error("endpoint must be IPv4:port");
    }
    const auto port = text.substr(colon + 1);
    if (port.empty() || port.size() > 5 || !std::all_of(port.begin(), port.end(), [](char c) {
            return c >= '0' && c <= '9';
        })) {
        throw std::runtime_error("invalid endpoint port");
    }
    const auto n = std::stoul(port);
    if (!n || n > 65535) {
        throw std::runtime_error("endpoint port must be 1..65535");
    }
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons(static_cast<uint16_t>(n));
    if (inet_pton(AF_INET, text.substr(0, colon).c_str(), &a.sin_addr) != 1) {
        throw std::runtime_error("endpoint must use numeric IPv4 address");
    }
    const auto host = ntohl(a.sin_addr.s_addr);
    if (host == 0 || host == 0xffffffff || (host >> 28) == 14) {
        throw std::runtime_error("Only explicit unicast endpoints are supported");
    }
    return a;
}

bool same(const sockaddr_in& a, const sockaddr_in& b) {
    return a.sin_family == b.sin_family && a.sin_port == b.sin_port &&
           a.sin_addr.s_addr == b.sin_addr.s_addr;
}

/** Sequence, duplicate and recovery state for one bounded finite session.
 * No message becomes readable until the full observed gap is filled. Transport
 * and CSV publication borrow this state but never mutate its buffers directly.
 */
class ReorderBuffer {
    const LiveOptions& options;
    std::map<uint64_t, Bytes> pending, recent;
    size_t pending_bytes = 0, recent_bytes = 0;
    uint64_t next = 1, high = 1;
    std::optional<uint64_t> end;
    std::optional<Clock::time_point> gap_start;
    Clock::time_point last_progress, last_request;

    void insert(uint64_t sequence, Bytes bytes) {
        if (sequence < next) {
            const auto old = recent.find(sequence);
            if (old != recent.end() && old->second != bytes) {
                fail("conflicting processed duplicate");
            }
            ++duplicates;
            return;
        }
        const auto old = pending.find(sequence);
        if (old != pending.end()) {
            if (old->second != bytes) {
                fail("conflicting buffered duplicate");
            }
            ++duplicates;
            return;
        }
        if (pending.size() >= options.max_messages ||
            bytes.size() > options.max_bytes - pending_bytes) {
            fail("reorder buffer limit exceeded");
        }
        pending_bytes += bytes.size();
        pending.emplace(sequence, std::move(bytes));
    }

  public:
    uint64_t requests = 0, duplicates = 0, gaps = 0;
    size_t peak_messages = 0, peak_bytes = 0;

    explicit ReorderBuffer(const LiveOptions& limits)
        : options(limits), last_progress(Clock::now()),
          last_request(last_progress - Ms(limits.retry_ms)) {}

    uint64_t next_sequence() const {
        return next;
    }

    bool has_pending() const {
        return !pending.empty();
    }

    bool complete() const {
        return end && next == *end;
    }

    [[noreturn]] void fail(const std::string& reason) const {
        std::cerr << "state=FAILED next_sequence=" << next << " highest_boundary=" << high
                  << " buffered_messages=" << pending.size() << '\n';
        throw std::runtime_error(reason + "; next_sequence=" + std::to_string(next));
    }

    void check_deadlines(Clock::time_point now) const {
        if (gap_start && now - *gap_start >= Ms(options.recovery_ms)) {
            fail("recovery timeout");
        }
        if (now - last_progress >= Ms(options.idle_ms)) {
            fail("feed progress timeout (freshness unknown)");
        }
    }

    /** Return the first missing range; a successful send must call requested(). */
    std::optional<std::pair<uint64_t, uint16_t>> recovery_request(Clock::time_point now) const {
        if (!gap_start || now - last_request < Ms(options.retry_ms)) {
            return std::nullopt;
        }
        uint64_t missing = next;
        for (const auto& entry : pending) {
            if (entry.first != missing) {
                break;
            }
            ++missing;
        }
        const auto following = pending.lower_bound(missing);
        const auto until = following == pending.end() ? high : following->first;
        const auto count = std::min<uint64_t>(until - missing, 65534);
        if (!count) {
            fail("invalid recovery range");
        }
        return std::make_pair(missing, static_cast<uint16_t>(count));
    }

    void requested(Clock::time_point now) {
        ++requests;
        last_request = now;
    }

    void admit(MoldDatagram datagram) {
        if (end && datagram.boundary > *end) {
            fail("data beyond end-of-session boundary");
        }
        if (datagram.ending) {
            if ((end && *end != datagram.sequence) || datagram.sequence < high) {
                fail("inconsistent end-of-session boundary");
            }
            end = datagram.sequence;
        }
        if (datagram.boundary > high) {
            high = datagram.boundary;
            last_progress = Clock::now();
        }
        for (size_t i = 0; i < datagram.messages.size(); ++i) {
            insert(datagram.sequence + i, std::move(datagram.messages[i]));
        }
        peak_messages = std::max(peak_messages, pending.size());
        peak_bytes = std::max(peak_bytes, pending_bytes);
    }

    bool ready() {
        // Every pending key is in [next, high); equal counts prove continuity.
        if (high - next != pending.size()) {
            if (!gap_start) {
                gap_start = Clock::now();
                ++gaps;
                last_request = *gap_start - Ms(options.retry_ms);
                std::cerr << "state=RECOVERING next_sequence=" << next
                          << " highest_boundary=" << high << '\n';
            }
            return false;
        }
        if (gap_start || next == 1) {
            std::cerr << "state=SYNCED next_sequence=" << next << '\n';
        }
        gap_start.reset();
        return true;
    }

    const Bytes& front() const {
        if (pending.empty() || pending.begin()->first != next) {
            fail("internal noncontiguous queue");
        }
        return pending.begin()->second;
    }

    /** Retire only a successfully applied/published frame and retain bounded duplicate evidence. */
    void acknowledge() {
        auto entry = pending.begin();
        pending_bytes -= entry->second.size();
        recent_bytes += entry->second.size();
        recent.emplace(next, std::move(entry->second));
        pending.erase(entry);
        while (recent.size() > options.max_messages || recent_bytes > options.max_bytes) {
            recent_bytes -= recent.begin()->second.size();
            recent.erase(recent.begin());
        }
        ++next;
        last_progress = Clock::now();
    }
};

/** Poll and validate one datagram; unrelated endpoints are counted and ignored. */
std::optional<MoldDatagram> receive_datagram(int socket, const sockaddr_in& source,
                                             const sockaddr_in& recovery,
                                             const std::string& session,
                                             const ReorderBuffer& buffer, uint64_t& ignored) {
    pollfd wait{socket, POLLIN, 0};
    const int ready = poll(&wait, 1, 10);
    if (ready < 0) {
        if (errno == EINTR) {
            return std::nullopt;
        }
        buffer.fail("socket poll failed");
    }
    if (!ready) {
        return std::nullopt;
    }
    if (wait.revents & (POLLERR | POLLHUP | POLLNVAL)) {
        buffer.fail("socket poll error");
    }
    std::array<unsigned char, 65536> packet{};
    sockaddr_in from{};
    socklen_t from_len = sizeof(from);
    const auto received = recvfrom(socket, packet.data(), packet.size(), 0,
                                   reinterpret_cast<sockaddr*>(&from), &from_len);
    if (received < 0) {
        if (errno == EINTR) {
            return std::nullopt;
        }
        buffer.fail("socket receive failed");
    }
    if (!same(from, source) && !same(from, recovery)) {
        ++ignored;
        return std::nullopt;
    }
    return parse_moldudp64(packet.data(), static_cast<size_t>(received), session);
}

/** Send a missing range before marking its retry deadline as advanced. */
void send_recovery(int socket, const sockaddr_in& endpoint, const std::string& session,
                   ReorderBuffer& buffer, Clock::time_point now) {
    const auto range = buffer.recovery_request(now);
    if (!range) {
        return;
    }
    std::array<unsigned char, 20> request{};
    std::copy(session.begin(), session.end(), request.begin());
    write_big_endian(request.data() + 10, range->first, 8);
    write_big_endian(request.data() + 18, range->second, 2);
    if (sendto(socket, request.data(), request.size(), 0,
               reinterpret_cast<const sockaddr*>(&endpoint), sizeof(endpoint)) != 20) {
        buffer.fail("cannot send recovery request");
    }
    buffer.requested(now);
    std::cerr << "recovery_request sequence=" << range->first << " count=" << range->second << '\n';
}

/** Apply one ordered frame and publish its selected book snapshot. */
void publish_frame(const ReorderBuffer& buffer, Book& book, std::ostream& output, size_t depth,
                   bool check_book, Result& result) {
    const auto event = decode(buffer.front(), buffer.next_sequence());
    if (event) {
        const auto snapshot = book.apply(*event, depth, check_book);
        if (snapshot) {
            const auto line = csv_line(*snapshot, false);
            output << line << '\n';
            if (!output) {
                buffer.fail("output write failed");
            }
            result.hash = hash_csv_line(result.hash, line);
            ++result.selected;
            ++result.emitted;
        }
    }
}

} // namespace

Result live(const LiveOptions& opt, const std::string& target, std::ostream& output, size_t depth,
            bool check_book) {
    if (opt.session.size() != 10 ||
        !std::all_of(opt.session.begin(), opt.session.end(),
                     [](unsigned char c) {
                         return c >= 32 && c <= 126;
                     }) ||
        !opt.idle_ms || !opt.recovery_ms || !opt.retry_ms || opt.retry_ms > opt.recovery_ms ||
        !opt.max_messages || !opt.max_bytes) {
        throw std::runtime_error("invalid live limits/session (session must be exactly 10 bytes)");
    }
    const auto listen = address(opt.listen), source = address(opt.source),
               recovery = address(opt.recovery);
    Socket socket;
    if (bind(socket.fd, reinterpret_cast<const sockaddr*>(&listen), sizeof(listen)) != 0) {
        throw std::runtime_error("cannot bind UDP socket: " + std::string(std::strerror(errno)));
    }
    Book book(target);
    Result result;
    result.hash = fnv1a_offset_basis;
    ReorderBuffer buffer(opt);
    uint64_t ignored = 0;
    const auto begin = Clock::now();
    std::cerr << "state=STARTING session=" << opt.session << " next_sequence=1\n";
    std::cerr << "listening=" << opt.listen
              << " transport=MoldUDP64-unicast validation=simulated-only\n";
    output << csv_header(false) << '\n';
    try {
        while (true) {
            const auto now = Clock::now();
            buffer.check_deadlines(now);
            send_recovery(socket.fd, recovery, opt.session, buffer, now);
            auto datagram =
                receive_datagram(socket.fd, source, recovery, opt.session, buffer, ignored);
            if (!datagram) {
                continue;
            }
            buffer.admit(std::move(*datagram));
            if (!buffer.ready()) {
                continue;
            }
            while (buffer.has_pending()) {
                publish_frame(buffer, book, output, depth, check_book, result);
                buffer.acknowledge();
                ++result.frames;
            }
            if (buffer.complete()) {
                break;
            }
        }
    } catch (const std::exception& error) {
        std::cerr << "state=UNTRUSTED next_sequence=" << buffer.next_sequence() << '\n';
        throw std::runtime_error(std::string(error.what()) +
                                 "; next_sequence=" + std::to_string(buffer.next_sequence()) +
                                 "; no complete session published");
    }
    result.elapsed_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - begin).count();
    std::cerr << "state=COMPLETE next_sequence=" << buffer.next_sequence()
              << " frames=" << result.frames << " selected=" << result.selected
              << " gaps=" << buffer.gaps << " requests=" << buffer.requests
              << " duplicates=" << buffer.duplicates << " ignored_sources=" << ignored
              << " peak_buffer_messages=" << buffer.peak_messages
              << " peak_buffer_bytes=" << buffer.peak_bytes
              << " validation=simulated-only nasdaq_connection=unverified\n";
    return result;
}
} // namespace itch
