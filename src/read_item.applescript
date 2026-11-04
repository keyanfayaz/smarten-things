use AppleScript version "2.5"
use framework "Foundation"
use scripting additions

-- Exact-ID, open-item reads only. No mutation commands or property records.
on run argv
    if (count of argv) is not 2 then error "Expected item kind and ID"
    set itemKind to item 1 of argv
    set itemID to item 2 of argv
    if itemKind is not in {"task", "project"} then error "Unsupported item kind"
    set resultDict to current application's NSMutableDictionary's alloc()'s init()
    set row to current application's NSMutableDictionary's alloc()'s init()
    with timeout of 60 seconds
        tell application "/Applications/Things3.app"
            set appVersion to version
            if itemKind is "project" then
                set itemRef to project id itemID
            else
                set itemRef to to do id itemID
                if class of itemRef is project then error "Project is outside task scope"
            end if
            if status of itemRef is not open then error "Closed item outside read scope"
            row's setObject:(id of itemRef) forKey:"id"
            row's setObject:(name of itemRef) forKey:"title"
            set taskNotes to notes of itemRef
            if taskNotes is missing value then set taskNotes to ""
            row's setObject:taskNotes forKey:"notes"
            row's setObject:"open" forKey:"status"
            set parentProject to project of itemRef
            if parentProject is missing value then
                row's setObject:(current application's NSNull's |null|()) forKey:"project"
            else
                set parentDict to current application's NSMutableDictionary's alloc()'s init()
                parentDict's setObject:(id of parentProject) forKey:"id"
                parentDict's setObject:(name of parentProject) forKey:"title"
                row's setObject:parentDict forKey:"project"
            end if
            row's setObject:(my dateValue(activation date of itemRef, true)) forKey:"scheduled_date"
            row's setObject:(my dateValue(due date of itemRef, true)) forKey:"deadline"
            row's setObject:(my dateValue(modification date of itemRef, false)) forKey:"modified_at"
        end tell
    end timeout
    resultDict's setObject:2 forKey:"schema_version"
    resultDict's setObject:"things" forKey:"source"
    resultDict's setObject:itemKind forKey:"kind"
    resultDict's setObject:appVersion forKey:"app_version"
    resultDict's setObject:((current application's NSTimeZone's localTimeZone()'s |name|()) as text) forKey:"time_zone"
    resultDict's setObject:row forKey:"item"
    set jsonData to current application's NSJSONSerialization's dataWithJSONObject:resultDict options:0 |error|:(missing value)
    if jsonData is missing value then error "JSON serialization failed"
    return (current application's NSString's alloc()'s initWithData:jsonData encoding:(current application's NSUTF8StringEncoding)) as text
end run

on dateValue(theDate, dateOnly)
    if theDate is missing value then return current application's NSNull's |null|()
    if dateOnly then
        set formatter to current application's NSDateFormatter's alloc()'s init()
        formatter's setLocale:(current application's NSLocale's localeWithLocaleIdentifier:"en_US_POSIX")
        formatter's setDateFormat:"yyyy-MM-dd"
    else
        set formatter to current application's NSISO8601DateFormatter's alloc()'s init()
    end if
    return (formatter's stringFromDate:theDate) as text
end dateValue
