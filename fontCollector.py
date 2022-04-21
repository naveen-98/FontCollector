import ass
import os
import re
import shutil
import subprocess
import sys
from argparse import ArgumentParser
from fontTools import ttLib
from matplotlib import font_manager
from pathlib import Path
from struct import error as struct_error
from typing import List, NamedTuple, Set

from colorama import Fore, init
init(convert=True)

__version__ = "0.3.4"

# GLOBAL VARIABLES
LINE_PATTERN = re.compile(r"(?:\{(?P<tags>[^}]*)\}?)?(?P<text>[^{]*)")
INT_PATTERN = re.compile(r"[+-]?\d+")

TAG_R_PATTERN = re.compile(r"\\r")
TAG_ITALIC_PATTERN = re.compile(r"\\i[0-9]+|\\i\+[0-9]+|\\i\-[0-9]+")
TAG_BOLD_PATTERN = re.compile(r"\\b[0-9]+|\\b\+[0-9]+|\\b\-[0-9]+")
TAG_FN_PATTERN = re.compile(r"(?<=\\fn)(.*?)(?=\)\\|\\|(?<=\w)(?=$)(?<=\w)(?=$))|(?<=fn)(.*?)(\()(.*?)(\))|(?<=\\fn)(.*?)(?=\\)|(?<=\\fn)(.*?)(?=\)$)|(?<=\\fn)(.*?)(?=\)\\)")


class Font(NamedTuple):
    fontPath: str
    fontName: str
    weight: int
    italic: bool

    def __eq__(self, other):
        return self.fontName == other.fontName and self.italic == other.italic and self.weight == other.weight

    def __hash__(self):
        return hash((self.fontName, self.italic, self.weight))

class AssStyle(NamedTuple):
    """
    AssStyle is an instance that does not only represent "[V4+ Styles]" section of an .ass script.
    It can also represent the style at X line.
    """
    fontName: str
    weight: int # a.k.a bold
    italic: bool

    def __eq__(self, other):
        return self.fontName == other.fontName and self.weight == other.weight and self.italic == other.italic

    def __str__(self):
        return "FontName: " + self.fontName + " Weight: " + str(self.weight) + " Italic: " + str(self.italic)


def isMkv(fileName:Path) -> bool:
    """
    Parameters:
        fileName (Path): The file name. Example: "example.mkv"
    Returns:
        True if the mkv is an mkv file, false in any others cases

    Thanks to https://github.com/TypesettingTools/Myaamori-Aegisub-Scripts/blob/f2a52ee38eeb60934175722fa9d7f2c2aae015c6/scripts/fontvalidator/fontvalidator.py#L414
    """
    with open(fileName, 'rb') as f:
        return f.read(4) == b'\x1a\x45\xdf\xa3'


def stripFontname(fontName:str) -> str:
    """
    Parameters:
        fontName (str): The font name.
    Returns:
        The font without an @ at the beginning

    Thanks to https://github.com/TypesettingTools/Myaamori-Aegisub-Scripts/blob/f2a52ee38eeb60934175722fa9d7f2c2aae015c6/scripts/fontvalidator/fontvalidator.py#L33
    """
    if fontName.startswith('@'):
        return fontName[1:]
    else:
        return fontName


def parseTags(tags: str, style: AssStyle) -> AssStyle:
    """
    Parameters:
        tags (str): A string containing tag. Ex: "\fnBell MT\r\i1\b1"
        style (AssStyle): The line style of the tags we received
    Returns:
        The new AssStyle according to the tags
    """
    tagsList = TAG_R_PATTERN.split(tags)

    cleanTag = tagsList[-1]

    if(cleanTag):
        bold = TAG_BOLD_PATTERN.findall(cleanTag)
        if(bold):
            # We do [-1], because we only want the last match
            boldNumber = int(INT_PATTERN.findall(bold[-1])[0])

            # Yes, that's not a good way to do it, but I did not find any other solution.
            if boldNumber <= 0:
                style = style._replace(weight=400)
            elif boldNumber == 1:
                style = style._replace(weight=700)
            elif 2 <= boldNumber <= 150:
                style = style._replace(weight=100)
            elif 151 <= boldNumber <= 250:
                style = style._replace(weight=200)
            elif 251 <= boldNumber <= 350:
                style = style._replace(weight=300)
            elif 351 <= boldNumber <= 450:
                style = style._replace(weight=400)
            elif 451 <= boldNumber <= 550:
                style = style._replace(weight=500)
            elif 551 <= boldNumber <= 650:
                style = style._replace(weight=600)
            elif 651 <= boldNumber <= 750:
                style = style._replace(weight=700)
            elif 751 <= boldNumber <= 850:
                style = style._replace(weight=800)
            elif 851 <= boldNumber:
                style = style._replace(weight=900)

        italic = TAG_ITALIC_PATTERN.findall(cleanTag)
        if(italic):
            # We do [-1], because we only want the last match
            italicNumber = INT_PATTERN.findall(italic[-1])

            if(italicNumber[0] == "1"):
                style = style._replace(italic=True)

        # Get the last occurence + the first element in the array.
        font = TAG_FN_PATTERN.findall(cleanTag)

        if(len(font) > 0 and len(font[-1]) > 0):
            font = font[-1][0]

            # Aegisub does not allow "(" or ")" in a fontName
            if("(" not in font and ")" not in font):
                style = style._replace(fontName=stripFontname(font.strip().lower()))
            else:
                print(Fore.LIGHTRED_EX + "Warning: fontName can not contains \"(\" or \")\"." + Fore.WHITE)

    return style


def parseLine(lineRawText: str, style: AssStyle) -> Set[AssStyle]:
    """
    Parameters:
        lineRawText (str): Ass line. Example: {\fnJester\b1}This is an example!
        style (AssStyle): Style applied to this line
    Returns:
        A set that contains all the possible style in a line.
    """
    allLineTags = ""
    styleSet = set()

    # The last match of the regex is useless, so we remove it
    for tags, text in LINE_PATTERN.findall(lineRawText)[:-1]:

        """
        I add \\} at each tags block.
        Example if I don't add \\}:
        {\b1\fnJester}FontCollectorTest{this is an commentaire} --> Would give me the fontName Jesterthis is an commentaire
        """
        allLineTags += tags + "\\}"
        styleSet.add(parseTags(allLineTags, style))

    return styleSet


def getAssStyle(subtitle: ass.Document, fileName:str) -> Set[AssStyle]:
    """
    Parameters:
        subtitle (ass.Document): Ass Document
        fileName (str): The file name of the ass. Ex: "test.ass"
    Returns:
        A set containing all style
    """
    styleSet = set()
    styles = {style.name: AssStyle(stripFontname(style.fontname.strip().lower()), 700 if style.bold else 400, style.italic)
              for style in subtitle.styles}

    for i, line in enumerate(subtitle.events):
        if(isinstance(line, ass.Dialogue)):
            try:
                style = styles[line.style]
                styleSet.update(parseLine(line.text, style))

            except KeyError:
                sys.exit(print(Fore.RED + f"Error: unknown style \"{line.style}\" on line {i+1}. You need to correct the .ass file named \"{fileName}\"" + Fore.WHITE))

    return styleSet


def copyFont(fontCollection: Set[Font], outputDirectory: Path):
    """
    Parameters:
        fontCollection (Set[Font]): Font collection
        outputDirectory (Path): The output directory where the font are going to be save
    """
    for font in fontCollection:
        shutil.copy(font.fontPath, outputDirectory)


def searchFont(fontCollection: Set[Font], style: AssStyle) -> List[Font]:
    """
    Parameters:
        fontCollection (Set[Font]): Font collection
        style (AssStyle): An AssStyle
    Returns:
        The font that match the best to the AssStyle
    """
    fontMatch = []

    for fontI in fontCollection:
        if(fontI.fontName == style.fontName):

            # I am not sure if it work like this in libass.
            if(fontI.weight < style.weight - 150 and style.weight <= 850):
                fontI = fontI._replace(weight=fontI.weight+150)

            fontMatch.append(fontI)

    # The sort is very important !
    if(style.italic):
        fontMatch.sort(key=lambda font: (-font.italic, abs(style.weight - font.weight), font.weight))
    else:
        fontMatch.sort(key=lambda font: (font.italic, abs(style.weight - font.weight), font.weight))

    return fontMatch


def findUsedFont(fontCollection: Set[Font], styleCollection: Set[AssStyle]) -> Set[Font]:
    """
    Parameters:
        fontCollection (Set[Font]): Font collection
        styleCollection (Set[AssStyle]): Style collection
    Returns:
        A set containing all the font that match the styleCollection
    """
    fontsMissing = set()
    fontsFound = set()

    for style in styleCollection:
        fontMatch = searchFont(fontCollection, style)

        if(len(fontMatch) != 0):
            fontsFound.add(fontMatch[0])
        else:
            fontsMissing.add(style.fontName)

    if(len(fontsMissing) > 0):
        print(Fore.RED + "\nError: Some fonts were not found. Are they installed? :")
        print("\n".join(fontsMissing))
        print(Fore.WHITE + "\n")
    else:
        print(Fore.LIGHTGREEN_EX + "All fonts found" + Fore.WHITE)

    return fontsFound


def createFont(fontPath: str) -> Font:
    """
    Parameters:
        fontPath (str): Font path. The font can be a .ttf, .otf or .ttc
    Returns:
        An Font object that represent the file at the fontPath
    """

    font = ttLib.TTFont(fontPath, fontNumber=0)

    details = {}
    for x in font['name'].names:
        try:
            details[x.nameID] = x.toStr()
        except UnicodeDecodeError:
            details[x.nameID] = x.string.decode(errors='ignore')

    # https://docs.microsoft.com/en-us/typography/opentype/spec/name#platform-encoding-and-language-ids
    fontName = details[1].strip().lower()

    try:
        # https://docs.microsoft.com/en-us/typography/opentype/spec/os2#fss
        isItalic = font["OS/2"].fsSelection & 0b1 > 0

        # https://docs.microsoft.com/en-us/typography/opentype/spec/os2#usweightclass
        weight = font['OS/2'].usWeightClass
    except struct_error:
        print(Fore.LIGHTRED_EX + f"Warning: The file \"{fontPath}\" does not have an OS/2 table. This can lead to minor errors." + Fore.WHITE)
        isItalic = False
        weight = 400

    # Some font designers appear to be under the impression that weights are 1-9 (From: https://github.com/Ristellise/AegisubDC/blob/master/src/font_file_lister_coretext.mm#L70)
    if(weight <= 9):
        weight *= 100

    return Font(fontPath, fontName, weight, isItalic)


def initializeFontCollection(additionalFontsDirectoryPath: List[str], additionalFontsFilePath: List[str]) -> Set[Font]:
    """
    Search all the installed font and save it in fontCollection
    Parameters:
        additionalFontsDirectoryPath (List[str]): List of directory which potentially contain fonts Ex: ["C:\\Users\\Admin\\Documents\\Personnal Font Collection"]
        additionalFontsFilePath (List[str]): List of file path Ex: ["C:\\Users\\Admin\\Desktop\\example_font.ttf"]
    Returns:
        List that contains all the font that the system could find including the one in additionalFontsDirectoryPath and additionalFontsFilePath
    """
    fontCollection = set()

    # TODO See if this line is required
    # font_manager._load_fontmanager(try_read_cache=False)

    # Even if I write ttf, it will also search for .otf and .ttc file
    fontsPath = font_manager.findSystemFonts(fontpaths=None, fontext='ttf')

    fontsPath.extend(additionalFontsFilePath)
    for fontPath in additionalFontsDirectoryPath:
        fontsPath.extend(font_manager.findSystemFonts(fontpaths=fontPath, fontext='ttf'))

    for fontPath in fontsPath:
        fontCollection.add(createFont(fontPath))

    return fontCollection


def deleteFonts(mkvFile:Path, mkvpropedit:Path):
    """
    Delete all mkv attached font

    Parameters:
        mkvFile (Path): Path to mkvFile
        mkvpropedit (Path): Path to mkvpropedit
    """
    mkvpropedit_args = [
        '"' + str(mkvFile) + '"'
        ]

    mkvpropedit_args.insert(0, str(mkvpropedit))

    # We only want to remove ttf, ttc or otf file
    # This is from mpv: https://github.com/mpv-player/mpv/blob/305332f8a06e174c5c45c9c4547293502ac7ecdb/sub/sd_ass.c#L101
    mkvpropedit_args.append("--delete-attachment mime-type:application/x-truetype-font")
    mkvpropedit_args.append("--delete-attachment mime-type:application/vnd.ms-opentype")
    mkvpropedit_args.append("--delete-attachment mime-type:application/x-font-ttf")
    mkvpropedit_args.append("--delete-attachment mime-type:application/x-font")
    mkvpropedit_args.append("--delete-attachment mime-type:application/font-sfnt")
    mkvpropedit_args.append("--delete-attachment mime-type:font/collection")
    mkvpropedit_args.append("--delete-attachment mime-type:font/otf")
    mkvpropedit_args.append("--delete-attachment mime-type:font/sfnt")
    mkvpropedit_args.append("--delete-attachment mime-type:font/ttf")

    subprocess.call(" ".join(mkvpropedit_args))
    print(Fore.LIGHTGREEN_EX + "Successfully deleted fonts with mkv" + Fore.WHITE)


def mergeFont(fontCollection: Set[Font], mkvFile: Path, mkvpropedit: Path):
    """
    Parameters:
        fontCollection (Path): All font needed to be merge in the mkv
        mkvFile (Path): Mkv file path
        mkvpropedit (Path): Mkvpropedit file path
    """
    mkvpropedit_args = [
        '"' + str(mkvFile) + '"'
        ]

    mkvpropedit_args.insert(0, str(mkvpropedit))


    for font in fontCollection:
        mkvpropedit_args.append("--add-attachment " + '"' + font.fontPath + '"')

    subprocess.call(" ".join(mkvpropedit_args))
    print(Fore.LIGHTGREEN_EX + "Successfully merging fonts with mkv" + Fore.WHITE)


def main():
    parser = ArgumentParser(description="FontCollector for Advanced SubStation Alpha file.")
    parser.add_argument('--input', '-i', required=True, metavar="[.ass file]", help="""
    Subtitles file. Must be an ASS file.
    """)
    parser.add_argument('-mkv', metavar="[.mkv input file]", help="""
    Video where the fonts will be merge. Must be a Matroska file.
    """)
    parser.add_argument('--output', '-o', nargs='?', const='', metavar="path", help="""
    Destination path of the font. If not specified, it will be the current path.
    """)
    parser.add_argument('-mkvpropedit', metavar="[path]", help="""
    Path to mkvpropedit.exe if not in variable environments. If -mkv is not specified, it will do nothing.
    """)
    parser.add_argument('--delete-fonts', '-d', action='store_true', help="""
    If -d is specified, it will delete the font attached to the mkv before merging the new needed font. If -mkv is not specified, it will do nothing.
    """)
    parser.add_argument('--additional-fonts', metavar="path", nargs='*', help="""
    May be a directory containing font files or a single font file.
    """)

    args = parser.parse_args()

    # Parse args
    if(os.path.isfile(args.input)):
        input = Path(args.input)

        split_tup = os.path.splitext(input)
        file_extension = split_tup[1]

        if(".ass" != file_extension):
            return print(Fore.RED + "Error: the input file is not an .ass file." + Fore.WHITE)
    else:
        return print(Fore.RED + "Error: the input file does not exist" + Fore.WHITE)

    output = ""
    if args.output is not None:

        output = Path(args.output)

        if not os.path.isdir(output):
            return print(Fore.RED + "Error: the output path is not a valid folder." + Fore.WHITE)

    mkvFile = ""
    if args.mkv is not None:
        mkvFile = Path(args.mkv)

        if not os.path.isfile(mkvFile):
            return print(Fore.RED + "Error: the mkv file specified does not exist." + Fore.WHITE)
        elif not isMkv(mkvFile):
            return print(Fore.RED + "Error: the mkv file specified is not an .mkv file." + Fore.WHITE)

    mkvpropedit = ""
    if mkvFile:
        if(args.mkvpropedit is not None and os.path.isfile(args.mkvpropedit)):
            mkvpropedit = Path(args.mkvpropedit)
        else:
            mkvpropedit = shutil.which("mkvpropedit")

            if not mkvpropedit:
                return print(Fore.RED + "Error: mkvpropedit in not in your environnements variable, add it or specify the path to mkvpropedit.exe with -mkvpropedit." + Fore.WHITE)

        delete_fonts = args.delete_fonts

    additionalFontsDirectoryPath = []
    additionalFontsFilePath = []
    if(args.additional_fonts is not None):
        for additional_font in args.additional_fonts:
            path = Path(additional_font)

            if path.is_dir():
                additionalFontsDirectoryPath.append(str(path))
            elif path.is_file():
                additionalFontsFilePath.append(str(path))

    fontCollection = initializeFontCollection(additionalFontsDirectoryPath, additionalFontsFilePath)

    with open(input, encoding='utf_8_sig') as f:
        subtitles = ass.parse(f)

    styleCollection = getAssStyle(subtitles, os.path.basename(input))

    fontsUsed = findUsedFont(fontCollection, styleCollection)

    if output:
        copyFont(fontsUsed, output)

    if mkvFile:
        if delete_fonts:
            deleteFonts(mkvFile, mkvpropedit)

        mergeFont(fontsUsed, mkvFile, mkvpropedit)


if __name__ == "__main__":
    sys.exit(main())