#pragma once
// GoW2 runtime settings (stand-in for Unleashed's config system; becomes a real config file later)
enum class ELanguage : uint32_t { English = 1, Japanese = 2, German = 3, French = 4, Spanish = 5, Italian = 6 };
template<typename T> struct ConfigValue
{
    T Value;
    operator T() const { return Value; }
    bool operator==(const T& o) const { return Value == o; }
};
struct Config
{
    static inline ConfigValue<ELanguage> Language{ ELanguage::English };
    static inline ConfigValue<bool> Vibration{ true };
};
