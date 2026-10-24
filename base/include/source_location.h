// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#ifndef BASE_INCLUDE_SOURCE_LOCATION_H_
#define BASE_INCLUDE_SOURCE_LOCATION_H_

#include <cstdint>
#include <ostream>
#include <string>

#if defined(_MSC_VER) && !defined(__clang__)
#if _MSC_VER >= 1926
#define BASE_SOURCE_LOCATION_HAS_BUILTIN_LOCATION 1
#define BASE_SOURCE_LOCATION_HAS_BUILTIN_COLUMN 1
#endif
#elif defined(__GNUC__) && !defined(__clang__)
// GCC has __builtin_FILE / LINE / FUNCTION but no __builtin_COLUMN (its
// __builtin_source_location needs the C++20 <source_location> types), so the
// column stays 0 here. Keyed on the version because GCC < 10 has no
// __has_builtin.
#if __GNUC__ >= 5
#define BASE_SOURCE_LOCATION_HAS_BUILTIN_LOCATION 1
#endif
#elif defined(__has_builtin)
#if __has_builtin(__builtin_LINE) && __has_builtin(__builtin_FILE) && \
    __has_builtin(__builtin_FUNCTION)
#define BASE_SOURCE_LOCATION_HAS_BUILTIN_LOCATION 1
#endif
#if __has_builtin(__builtin_COLUMN)
#define BASE_SOURCE_LOCATION_HAS_BUILTIN_COLUMN 1
#endif
#endif

#ifndef BASE_SOURCE_LOCATION_HAS_BUILTIN_LOCATION
#define BASE_SOURCE_LOCATION_HAS_BUILTIN_LOCATION 0
#endif
#ifndef BASE_SOURCE_LOCATION_HAS_BUILTIN_COLUMN
#define BASE_SOURCE_LOCATION_HAS_BUILTIN_COLUMN 0
#endif

#if BASE_SOURCE_LOCATION_HAS_BUILTIN_LOCATION
#define BASE_SOURCE_LOCATION_DETAIL_FILE() __builtin_FILE()
#define BASE_SOURCE_LOCATION_DETAIL_FUNCTION() __builtin_FUNCTION()
#define BASE_SOURCE_LOCATION_DETAIL_LINE() \
  static_cast<line_type>(__builtin_LINE())
#else
#define BASE_SOURCE_LOCATION_DETAIL_FILE() ""
#define BASE_SOURCE_LOCATION_DETAIL_FUNCTION() ""
#define BASE_SOURCE_LOCATION_DETAIL_LINE() \
  line_type { 0 }
#endif

#if BASE_SOURCE_LOCATION_HAS_BUILTIN_COLUMN
#define BASE_SOURCE_LOCATION_DETAIL_COLUMN() \
  static_cast<line_type>(__builtin_COLUMN())
#else
#define BASE_SOURCE_LOCATION_DETAIL_COLUMN() \
  line_type { 0 }
#endif

namespace lynx {
namespace base {

namespace detail {
constexpr bool str_equal(const char* a, const char* b) noexcept {
  while (*a != '\0' && *a == *b) {
    ++a;
    ++b;
  }
  return *a == *b;
}

}  // namespace detail

class source_location {
 public:
  using line_type = std::uint_least32_t;

  // An empty position: no file, no function, line and column 0.
  constexpr source_location() noexcept = default;

  // Builds a position by hand. A null file or function is treated as "".
  // Pass column 0 when it is unknown.
  constexpr source_location(const char* file, const char* function,
                            line_type line, line_type column = 0) noexcept
      : file_(file != nullptr ? file : ""),
        function_(function != nullptr ? function : ""),
        line_(line),
        column_(column) {}

  // Capture the position of the call site. The arguments must not be passed
  // explicitly; they exist only so the compiler evaluates the builtins where
  // current() is called (or where the enclosing function is called, when
  // current() is itself a default argument).
  static constexpr source_location current(
      const char* file = BASE_SOURCE_LOCATION_DETAIL_FILE(),
      const char* function = BASE_SOURCE_LOCATION_DETAIL_FUNCTION(),
      line_type line = BASE_SOURCE_LOCATION_DETAIL_LINE(),
      line_type column = BASE_SOURCE_LOCATION_DETAIL_COLUMN()) noexcept {
    return source_location(file, function, line, column);
  }

  constexpr const char* file_name() const noexcept { return file_; }
  constexpr const char* function_name() const noexcept { return function_; }
  constexpr line_type line() const noexcept { return line_; }
  constexpr line_type column() const noexcept { return column_; }

  // file_name() without its directory part ('/' and '\\' both separate).
  constexpr const char* file_basename() const noexcept {
    const char* base = file_;
    for (const char* p = file_; *p != '\0'; ++p) {
      if (*p == '/' || *p == '\\') base = p + 1;
    }
    return base;
  }

  // True for a default-constructed position.
  constexpr bool empty() const noexcept {
    return line_ == 0 && column_ == 0 && *file_ == '\0' && *function_ == '\0';
  }

  // "file:line:column (function)"; column and function are left out when
  // unknown, and an empty position prints as "<unknown>".
  std::string to_string() const {
    if (empty()) return "<unknown>";
    std::string s = file_;
    s += ':';
    s += std::to_string(line_);
    if (column_ != 0) {
      s += ':';
      s += std::to_string(column_);
    }
    if (*function_ != '\0') {
      s += " (";
      s += function_;
      s += ')';
    }
    return s;
  }

  // Compares by content, not by pointer, so equal strings from different
  // translation units still compare equal.
  friend constexpr bool operator==(const source_location& a,
                                   const source_location& b) noexcept {
    return a.line_ == b.line_ && a.column_ == b.column_ &&
           detail::str_equal(a.file_, b.file_) &&
           detail::str_equal(a.function_, b.function_);
  }

  friend constexpr bool operator!=(const source_location& a,
                                   const source_location& b) noexcept {
    return !(a == b);
  }

  friend std::ostream& operator<<(std::ostream& os, const source_location& p) {
    return os << p.to_string();
  }

 private:
  const char* file_ = "";
  const char* function_ = "";
  line_type line_ = 0;
  line_type column_ = 0;
};

}  // namespace base
}  // namespace lynx

#undef BASE_SOURCE_LOCATION_DETAIL_FILE
#undef BASE_SOURCE_LOCATION_DETAIL_FUNCTION
#undef BASE_SOURCE_LOCATION_DETAIL_LINE
#undef BASE_SOURCE_LOCATION_DETAIL_COLUMN

// Position of the expression itself, usable on every compiler. Without
// builtins it falls back to __FILE__ / __func__ / __LINE__ (no column), and
// it can only be used inside a function body.
#if BASE_SOURCE_LOCATION_HAS_BUILTIN_LOCATION
#define BASE_SOURCE_LOCATION_HERE() (::lynx::base::source_location::current())
#else
#define BASE_SOURCE_LOCATION_HERE() \
  (::lynx::base::source_location(__FILE__, __func__, __LINE__))
#endif

#endif  // BASE_INCLUDE_SOURCE_LOCATION_H_
