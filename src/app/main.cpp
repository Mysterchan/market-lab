#include "itch/app/options.hpp"
#include "itch/common/file_utils.hpp"
#include "itch/feed/moldudp/live.hpp"
#include "itch/output/staged_output.hpp"
#include "itch/replay/replay.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <sys/resource.h>

int main(int argc, char** argv) {
    try {
        const itch::Options options = itch::parse_options(argc, argv);
        if (options.live) {
            std::optional<itch::StagedOutput> staged;
            if (!options.output.path.empty()) {
                staged.emplace(options.output.path, "");
            }
            itch::live(options.network, options.book.target, staged ? staged->stream() : std::cout,
                       options.book.max_depth, options.book.check_book);
            auto& output = staged ? staged->stream() : std::cout;
            output.flush();
            if (!output) {
                throw std::runtime_error("output write failed");
            }
            if (staged) {
                staged->commit();
            }
            return 0;
        }
        itch::reject_same_file(options.replay.path, options.checkpoint.path);
        itch::reject_same_file(options.replay.path, options.checkpoint.resume_path);
        itch::reject_same_file(options.output.path, options.checkpoint.path);
        itch::reject_same_file(options.output.path, options.checkpoint.resume_path);
        // Replacing the loaded checkpoint is allowed after successful continuation.
        std::optional<itch::Checkpoint> resume;
        if (!options.checkpoint.resume_path.empty()) {
            resume = itch::load_checkpoint(options.checkpoint.resume_path);
        }
        if (resume && !resume->csv_path.empty()) {
            for (const auto& path : {options.replay.path, options.output.path,
                                     options.checkpoint.path, options.checkpoint.resume_path}) {
                itch::reject_same_file(path, resume->csv_path);
            }
        }
        itch::Checkpoint checkpoint;
        itch::ReplayControl control;
        control.resume = resume ? &*resume : nullptr;
        control.stop_after_frame = options.checkpoint.stop_after_frame;
        control.checkpoint = options.checkpoint.path.empty() && !options.checkpoint.resume_csv
                                 ? nullptr
                                 : &checkpoint;
        std::optional<itch::StagedOutput> staged;
        if (!options.output.path.empty()) {
            staged.emplace(options.output.path, options.replay.path);
        }
        std::optional<itch::StagedOutput> journal;
        const bool recoverable_csv =
            !options.output.path.empty() &&
            (options.checkpoint.resume_csv || (!options.checkpoint.path.empty() && !resume));
        if (recoverable_csv) {
            journal.emplace(options.output.path + ".recovery", options.replay.path);
            if (options.checkpoint.resume_csv) {
                itch::copy_checkpoint_csv(*resume, journal->stream());
            }
            control.omit_header = options.checkpoint.resume_csv;
        }
        auto bind_csv = [&](const itch::Checkpoint& state) {
            auto saved = state;
            if (journal) {
                journal->sync();
                const auto position = journal->stream().tellp();
                if (position < 0) {
                    throw std::runtime_error("cannot determine CSV prefix length");
                }
                saved.csv_path = std::filesystem::absolute(journal->temporary_path()).string();
                saved.csv_bytes = static_cast<uint64_t>(position);
            }
            return saved;
        };
        auto publish_checkpoint = [&](const itch::Checkpoint& state) {
            itch::save_checkpoint(bind_csv(state), options.checkpoint.path, options.replay.path);
            // Only retain a journal once a successfully published checkpoint references it.
            if (journal) {
                journal->preserve_temporary();
            }
        };
        if (options.checkpoint.every_frames) {
            control.checkpoint_every_frames = options.checkpoint.every_frames;
            control.on_checkpoint = publish_checkpoint;
        }

        const itch::Result first =
            itch::replay(options.replay.path, options.book.target,
                         journal  ? &journal->stream()
                         : staged ? &staged->stream()
                                  : &std::cout,
                         options.book.max_depth, options.book.check_book,
                         options.replay.allow_implicit_eof, options.replay.with_offset, control);

        if (options.replay.verify) {
            itch::Checkpoint second_checkpoint;
            itch::ReplayControl second_control;
            second_control.resume = control.resume;
            second_control.stop_after_frame = control.stop_after_frame;
            second_control.checkpoint = control.checkpoint ? &second_checkpoint : nullptr;
            const itch::Result second = itch::replay(
                options.replay.path, options.book.target, nullptr, options.book.max_depth,
                options.book.check_book, options.replay.allow_implicit_eof,
                options.replay.with_offset, second_control);
            if (first.hash != second.hash || first.selected != second.selected ||
                first.frames != second.frames) {
                throw std::runtime_error("deterministic replay mismatch");
            }
            if (control.checkpoint &&
                itch::encode_checkpoint(checkpoint) != itch::encode_checkpoint(second_checkpoint)) {
                throw std::runtime_error("deterministic checkpoint mismatch");
            }
        }

        if (journal) {
            itch::copy_checkpoint_csv(bind_csv(checkpoint), staged->stream());
        }
        std::ostream& output = staged ? staged->stream() : std::cout;
        output.flush();
        if (!output) {
            throw std::runtime_error("output write failed");
        }
        if (!options.checkpoint.path.empty()) {
            publish_checkpoint(checkpoint);
        }
        if (staged) {
            staged->commit();
        }

        rusage usage{};
        getrusage(RUSAGE_SELF, &usage);
#ifdef __APPLE__
        const uint64_t peak_bytes = static_cast<uint64_t>(usage.ru_maxrss);
#else
        const uint64_t peak_bytes = static_cast<uint64_t>(usage.ru_maxrss) * 1024;
#endif

        std::cerr << "frames=" << first.frames << " selected=" << first.selected
                  << " hash=" << std::hex << first.hash << std::dec
                  << " verified=" << (options.replay.verify ? "yes" : "no") << " scope="
                  << (resume && !options.checkpoint.resume_csv ? "suffix"
                      : first.stopped                          ? "prefix"
                                                               : "full")
                  << " complete=" << (first.stopped ? "no" : "yes") << " emitted=" << first.emitted
                  << " elapsed_ms=" << first.elapsed_ns / 1e6 << " frames_per_s="
                  << (first.elapsed_ns ? (first.frames - (resume ? resume->next_frame - 1 : 0)) *
                                             1e9 / first.elapsed_ns
                                       : 0)
                  << " mean_selected_event_ns="
                  << (first.emitted ? first.event_ns / first.emitted : 0)
                  << " max_selected_event_ns=" << first.max_event_ns
                  << " peak_rss_bytes=" << peak_bytes << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
