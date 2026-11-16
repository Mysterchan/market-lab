#pragma once

#include <fstream>
#include <functional>
#include <string>

namespace itch {

/** Own a temporary file beside a destination; publish only after explicit successful commit.
 * Normal destruction removes unpublished data unless preserve_temporary() retained a journal.
 * A process crash can leave a temporary file for offline checkpoint recovery.
 */
class StagedOutput {
  public:
    StagedOutput(const std::string& destination, const std::string& input);
    ~StagedOutput();

    StagedOutput(const StagedOutput&) = delete;
    StagedOutput& operator=(const StagedOutput&) = delete;

    std::ostream& stream() {
        return stream_;
    }

    /** Flush/close, optionally validate the temporary path, then atomically rename to destination.
     * Failure leaves the existing destination unchanged; throws std::runtime_error.
     */
    void commit(const std::function<void(const std::string&)>& validate = {});
    /** Flush and fsync the temporary file before a checkpoint binds to its prefix. */
    void sync();

    const std::string& temporary_path() const {
        return temporary_;
    }

    /** Keep the journal after destruction once a published checkpoint references it. */
    void preserve_temporary() {
        preserved_ = true;
    }

  private:
    std::string destination_;
    std::string temporary_;
    std::ofstream stream_;
    bool committed_ = false;
    bool preserved_ = false;
};

} // namespace itch
