# preymaker_sample

please run grab.sh first - github gets weird about uploaded zip files.

the first class unzips a large zip file, and as that png is generated its stored to a temp file and then overlayed on a composite image, and then immediately deleted.

the second just queries the MTA train service alerts public api and get active subway alerts based upon select trains. It prints these alerts to terminal, colored by enum.

Feel free to look at other projects for examples of database, aws integration, etc. as i update my github :)

I added some psuedocode for postgres integration and another way of approaching the image processing, there's some credential hardcoding which would // ***never*** // happen in a production or dev environment.
