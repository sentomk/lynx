// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#ifndef DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_CODEC_H_
#define DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_CODEC_H_

#include <cstdint>
#include <initializer_list>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "third_party/jsoncpp/include/json/json.h"

namespace lynx {
namespace devtool {
namespace cdp {

// Building blocks for the CDP types generated into cdp_protocol.generated.cc.
//
// Decoding follows the runtime contract: declared fields are checked for
// presence and type, unknown fields are ignored. The first mismatch fails the
// decode and is described as "<path>: <problem>", for example
// "params.value: expected string".
//
// Generated code adds DecodeValue / EncodeValue overloads for its own structs
// in the domain namespace; the container templates below find them through
// argument-dependent lookup.

// Allowed JSON kinds for fields stored as Json::Value.
enum Kind : uint16_t {
  kString = 1 << 0,
  kInteger = 1 << 1,
  kNumber = 1 << 2,
  kBoolean = 1 << 3,
  kObject = 1 << 4,
  kArray = 1 << 5,
  kNull = 1 << 6,
};

/**
 * Holds a generated struct on the heap so recursive protocol types, such as a
 * DOM.Node whose contentDocument is another Node, can contain themselves.
 *
 * Box has value semantics: copies are deep. It allocates lazily, so a default
 * or moved-from Box reads as a default-constructed T and recursive types never
 * expand without bound.
 */
template <typename T>
class Box {
 public:
  Box() = default;
  // Implicit so a T can initialize a Box or std::optional<Box<T>> field.
  Box(T value) : value_(std::make_unique<T>(std::move(value))) {}
  Box(const Box& other)
      : value_(other.value_ ? std::make_unique<T>(*other.value_) : nullptr) {}
  Box(Box&& other) noexcept = default;
  Box& operator=(const Box& other) {
    if (this != &other) {
      value_ = other.value_ ? std::make_unique<T>(*other.value_) : nullptr;
    }
    return *this;
  }
  Box& operator=(Box&& other) noexcept = default;

  const T& operator*() const { return value_ ? *value_ : Default(); }
  T& operator*() {
    if (!value_) {
      value_ = std::make_unique<T>();
    }
    return *value_;
  }
  const T* operator->() const { return &**this; }
  T* operator->() { return &**this; }

 private:
  static const T& Default() {
    static const T* value = new T();
    return *value;
  }

  std::unique_ptr<T> value_;
};

// Declared first so nested containers, such as a vector of maps, resolve.
template <typename T>
bool DecodeValue(const Json::Value& in, const std::string& path,
                 std::vector<T>* out, std::string* error);
template <typename T>
bool DecodeValue(const Json::Value& in, const std::string& path,
                 std::map<std::string, T>* out, std::string* error);
template <typename T>
bool DecodeValue(const Json::Value& in, const std::string& path, Box<T>* out,
                 std::string* error);
template <typename T>
Json::Value EncodeValue(const std::vector<T>& values);
template <typename T>
Json::Value EncodeValue(const std::map<std::string, T>& values);
template <typename T>
Json::Value EncodeValue(const Box<T>& value);

inline bool Fail(const std::string& path, const std::string& problem,
                 std::string* error) {
  *error = path + ": " + problem;
  return false;
}

inline const Json::Value& EmptyObject() {
  static const Json::Value kEmpty(Json::objectValue);
  return kEmpty;
}

inline bool DecodeValue(const Json::Value& in, const std::string& path,
                        std::string* out, std::string* error) {
  if (!in.isString()) {
    return Fail(path, "expected string", error);
  }
  *out = in.asString();
  return true;
}

inline bool DecodeValue(const Json::Value& in, const std::string& path,
                        int64_t* out, std::string* error) {
  if (in.type() == Json::intValue ||
      (in.type() == Json::uintValue && in.isInt64())) {
    *out = in.asInt64();
    return true;
  }
  return Fail(path, "expected integer", error);
}

inline bool DecodeValue(const Json::Value& in, const std::string& path,
                        double* out, std::string* error) {
  if (in.type() != Json::intValue && in.type() != Json::uintValue &&
      in.type() != Json::realValue) {
    return Fail(path, "expected number", error);
  }
  *out = in.asDouble();
  return true;
}

inline bool DecodeValue(const Json::Value& in, const std::string& path,
                        bool* out, std::string* error) {
  if (!in.isBool()) {
    return Fail(path, "expected boolean", error);
  }
  *out = in.asBool();
  return true;
}

// `any`: every JSON value is accepted.
inline bool DecodeValue(const Json::Value& in, const std::string& path,
                        Json::Value* out, std::string* error) {
  *out = in;
  return true;
}

template <typename T>
bool DecodeValue(const Json::Value& in, const std::string& path,
                 std::vector<T>* out, std::string* error) {
  if (!in.isArray()) {
    return Fail(path, "expected array", error);
  }
  out->clear();
  out->reserve(in.size());
  for (Json::ArrayIndex i = 0; i < in.size(); ++i) {
    T item{};
    if (!DecodeValue(in[i], path + "[" + std::to_string(i) + "]", &item,
                     error)) {
      return false;
    }
    out->push_back(std::move(item));
  }
  return true;
}

template <typename T>
bool DecodeValue(const Json::Value& in, const std::string& path,
                 std::map<std::string, T>* out, std::string* error) {
  if (!in.isObject()) {
    return Fail(path, "expected object", error);
  }
  out->clear();
  for (const auto& name : in.getMemberNames()) {
    T item{};
    if (!DecodeValue(in[name], path + "." + name, &item, error)) {
      return false;
    }
    out->emplace(name, std::move(item));
  }
  return true;
}

template <typename T>
bool DecodeValue(const Json::Value& in, const std::string& path, Box<T>* out,
                 std::string* error) {
  return DecodeValue(in, path, &**out, error);
}

template <typename T>
bool DecodeField(const Json::Value& object, const char* name,
                 const std::string& path, T* out, std::string* error) {
  const std::string field_path = path + "." + name;
  if (!object.isMember(name)) {
    return Fail(field_path, "missing required field", error);
  }
  return DecodeValue(object[name], field_path, out, error);
}

template <typename T>
bool DecodeField(const Json::Value& object, const char* name,
                 const std::string& path, std::optional<T>* out,
                 std::string* error) {
  if (!object.isMember(name)) {
    out->reset();
    return true;
  }
  T value{};
  if (!DecodeValue(object[name], path + "." + name, &value, error)) {
    return false;
  }
  *out = std::move(value);
  return true;
}

// Checks a Json::Value field against the declared kinds, for unions and
// objects without declared properties.
inline bool CheckKinds(const Json::Value& value, const std::string& path,
                       uint16_t kinds, std::string* error) {
  uint16_t kind = 0;
  switch (value.type()) {
    case Json::nullValue:
      kind = kNull;
      break;
    case Json::intValue:
    case Json::uintValue:
      kind = kInteger | kNumber;
      break;
    case Json::realValue:
      kind = kNumber;
      break;
    case Json::stringValue:
      kind = kString;
      break;
    case Json::booleanValue:
      kind = kBoolean;
      break;
    case Json::arrayValue:
      kind = kArray;
      break;
    case Json::objectValue:
      kind = kObject;
      break;
  }
  if (kind & kinds) {
    return true;
  }
  return Fail(path, "has an unexpected type", error);
}

inline bool CheckKinds(const std::optional<Json::Value>& value,
                       const std::string& path, uint16_t kinds,
                       std::string* error) {
  return !value || CheckKinds(*value, path, kinds, error);
}

inline bool CheckEnum(const std::string& value, const std::string& path,
                      std::initializer_list<const char*> allowed,
                      std::string* error) {
  for (const char* candidate : allowed) {
    if (value == candidate) {
      return true;
    }
  }
  return Fail(path, "'" + value + "' is not an allowed value", error);
}

inline bool CheckEnum(const std::optional<std::string>& value,
                      const std::string& path,
                      std::initializer_list<const char*> allowed,
                      std::string* error) {
  return !value || CheckEnum(*value, path, allowed, error);
}

// Runs |check(element, element_path)| on every element of a container, for
// constraints that the C++ element type cannot express, such as enums.
template <typename T, typename Check>
bool CheckEach(const std::vector<T>& values, const std::string& path,
               Check check) {
  for (size_t i = 0; i < values.size(); ++i) {
    if (!check(values[i], path + "[" + std::to_string(i) + "]")) {
      return false;
    }
  }
  return true;
}

template <typename T, typename Check>
bool CheckEach(const std::map<std::string, T>& values, const std::string& path,
               Check check) {
  for (const auto& [name, value] : values) {
    if (!check(value, path + "." + name)) {
      return false;
    }
  }
  return true;
}

inline Json::Value EncodeValue(const std::string& value) {
  return Json::Value(value);
}

inline Json::Value EncodeValue(int64_t value) {
  return Json::Value(static_cast<Json::Int64>(value));
}

inline Json::Value EncodeValue(double value) { return Json::Value(value); }

inline Json::Value EncodeValue(bool value) { return Json::Value(value); }

inline Json::Value EncodeValue(const Json::Value& value) { return value; }

template <typename T>
Json::Value EncodeValue(const std::vector<T>& values) {
  Json::Value out(Json::arrayValue);
  for (const auto& value : values) {
    out.append(EncodeValue(value));
  }
  return out;
}

template <typename T>
Json::Value EncodeValue(const std::map<std::string, T>& values) {
  Json::Value out(Json::objectValue);
  for (const auto& [name, value] : values) {
    out[name] = EncodeValue(value);
  }
  return out;
}

template <typename T>
Json::Value EncodeValue(const Box<T>& value) {
  return EncodeValue(*value);
}

template <typename T>
void EncodeField(Json::Value* object, const char* name, const T& value) {
  (*object)[name] = EncodeValue(value);
}

template <typename T>
void EncodeField(Json::Value* object, const char* name,
                 const std::optional<T>& value) {
  if (value) {
    (*object)[name] = EncodeValue(*value);
  }
}

}  // namespace cdp
}  // namespace devtool
}  // namespace lynx

#endif  // DEVTOOL_BASE_DEVTOOL_NATIVE_PUBLIC_CDP_CODEC_H_
